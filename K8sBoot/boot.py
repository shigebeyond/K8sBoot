#!/usr/bin/python3
# -*- coding: utf-8 -*-

import fnmatch
import hashlib
import json
import os
import re
from itertools import groupby
from functools import wraps
from urllib import parse
from pyutilb.util import *
from pyutilb.file import *
from pyutilb.cmd import *
from pyutilb import YamlBoot, BreakException
from pyutilb.log import log

# 在动作参数上进行变量替换，这样支持参数类型为str或dict或list等
def replace_var_on_params(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        args = [replace_var(arg, False) for arg in args]
        result = func(self, *args, **kwargs)
        return result
    return wrapper

'''
k8s配置生成的基于yaml的启动器
参考：
k8s配置文件demo: https://gitee.com/czy233/k8s-demo
k8s资源简写: https://zhuanlan.zhihu.com/p/369647740
'''
class Boot(YamlBoot):

    fullnames = {
        'rc': 'ReplicationController',
        'rs': 'ReplicaSet',
        'ds': 'DaemonSet',
        'sts': 'StatefulSet',
        'deploy': 'Deployment',
    }

    def __init__(self):
        super().__init__()
        # step_dir作为当前目录
        self.step_dir_as_cwd = True
        # 动作映射函数
        actions = {
            'ns': self.ns,
            'app': self.app,
            'labels': self.labels,
            'config': self.config,
            'config_from_files': self.config_from_files,
            'secret': self.secret,
            'secret_files': self.secret_files,
            'pod': self.pod,
            'rc': self.rc,
            'rs': self.rs,
            'ds': self.ds,
            'sts': self.sts,
            'deploy': self.deploy,
            'hpa': self.hpa,
            'ingress': self.ingress,
            'initContainers': self.initContainers,
            'containers': self.containers,
        }
        self.add_actions(actions)
        # 自定义函数
        funcs = {
            'ref_pod_field': self.ref_pod_field,
            'ref_resource_field': self.ref_resource_field,
            'ref_config': self.ref_config,
            'ref_secret': self.ref_secret,
        }
        custom_funs.update(funcs)

        self._ns = '' # 命名空间
        self._app = '' # 应用名
        self._labels = {}  # 记录标签
        self._config_data = {} # 记录设置过的配置
        self._config_file_keys = [] # 记录文件类型的key
        self._secret_data = {} # 记录设置过的密文
        self._secret_file_keys = [] # 记录文件类型的key
        self._init_containers = None # 记录处理过的初始容器
        self._containers = [] # 记录处理过的容器
        self._volumes = {} # 记录容器中的卷，key是卷名，value是卷信息
        self._ports = [] # 记录容器中的端口映射
        self._service_type2ports = {} # 记录service类型对端口映射
        self._is_sts = False # 是否用 statefulset 来部署

    # 清空app相关的属性
    def clear_app(self):
        self._app = ''  # 应用名
        set_var('app', None)
        self._labels = {}  # 记录标签
        self._config_data = {}  # 记录设置过的配置
        self._config_file_keys = [] # 记录文件类型的key
        self._secret_data = {}  # 记录设置过的密文
        self._secret_file_keys = [] # 记录文件类型的key
        self._init_containers = None  # 记录处理过的初始容器
        self._containers = []  # 记录处理过的容器
        self._volumes = {}  # 记录容器中的卷，key是卷名，value是卷信息
        self._ports = []  # 记录容器中的端口映射
        self._service_type2ports = {}  # 记service类型对端口映射
        self._is_sts = False  # 是否用 statefulset 来部署

    # 保存yaml
    def save_yaml(self, data, file_postfix):
        # 转yaml
        if isinstance(data, list): # 多个资源
            data = list(map(yaml.dump, data))
            data = "\n---\n\n".join(data)
        elif not isinstance(data, str):
            data = yaml.dump(data)
        # 创建目录
        if not os.path.exists(self._app):
            os.makedirs(self._app)
        # 保存文件
        file = os.path.join(self._app, self._app + file_postfix)
        write_file(file, data)

    def print_apply_cmd(self):
        '''
        打印 kubectl apply 命令
        '''
        dir = os.path.abspath(self._app)
        cmd = f'App[{self._app}]的资源配置文件已生成完毕, 如要更新到集群中的资源请手动执行: kubectl apply --record=true -f {dir}'
        log.info(cmd)

    # --------- 动作处理的函数 --------
    # 修正节点标签
    def node_labels(self, config):
        old_node2labels = self.get_node2labels()
        for node, new_labels in config.items():
            if node in old_node2labels:
                old_labels = old_node2labels[node]
            else:
                old_labels = {}
            old_keys = old_labels.keys()
            new_keys = new_labels.keys()
            # 添加label
            add_keys = new_keys - old_keys
            # 删除label
            del_keys = old_keys - new_keys
            # 修改label
            same_keys = old_keys & new_keys
            for key in same_keys:
                if old_labels[key] != new_labels[key]:
                    pass

    # 获得节点现有标签
    def get_node2labels(self):
        df = run_command_return_dataframe(f"kubectl get no  --show-labels")
        node2labels = {}
        for i, row in df.iterrows():
            # 将 kubernetes.io/role=worker 变为 /role=worker
            labels = re.sub(r'[^,]+\/', '', row['LABELS']).split(',')
            labeldict = {}
            for label in labels:
                key, val = label.split('=')
                labeldict[key] = val
            node2labels[df['NAME']] = labeldict
        return node2labels

    # 设置与生成命名空间
    def ns(self, name):
        if self._ns != '':
            raise Exception('已设置过命名空间, 仅支持唯一的命名空间')
        self._ns = name
        yaml = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": self._ns,
        }
        self.save_yaml(yaml, '-ns.yml')

    def app(self, steps, name=None):
        '''
        处理应用
        :param steps 子步骤
        name 应用名
        '''
        self._app = name
        self._labels = {
            'app': self._app
        }
        # 执行子步骤
        self.run_steps(steps)
        # 生成configmap
        self.configmap()
        # 生成secret
        self.secretmap()
        # 生成service：暴露端口
        self.service()
        # 打印 kubectl apply 命令
        self.print_apply_cmd()
        # 清空app相关的属性
        self.clear_app()

    @replace_var_on_params
    def labels(self, lbs):
        '''
        设置应用标签
        :param lbs:
        :return:
        '''
        self._labels.update(lbs)

    @replace_var_on_params
    def config(self, data):
        '''
        以键值对的方式来设置配置
        :param data 配置项，可以是键值对，可以包含变量，如
              name: shigebeyond
              nginx.conf: ${read_file(./nginx.conf)}
              也可以是变量表达式，如 $cfg 或 ${read_yaml(./cfg.yml)}
        '''
        if not isinstance(data, dict):
            raise Exception('config动作参数只接受dict类型')
        self._config_data.update(data)

    @replace_var_on_params
    def config_from_files(self, files):
        '''
        以文件内容的方式来设置配置，在挂载configmap时items默认填充用config_from_files()写入的key
        :param files 配置文件list或dict或目录
                  dict类型： key是配置项名，value是文件路径，如 nginx.conf: ./nginx.conf
                  list类型： 元素是文件路径，会用文件名作为key
                  str类型： 目录/文件路径
        '''
        files = self.build_config_data_from_files(files)
        self._config_file_keys.extend(files.keys())
        self._config_data.update(files)

    def build_config_data_from_files(self, files):
        '''
        以读文件内容的方式来构建配置数据
        :param files 配置文件list或dict或目录
                  dict类型： key是配置项名，value是文件路径，如 nginx.conf: ./nginx.conf
                  list类型： 元素是文件路径，会用文件名作为key
                  str类型： 目录/文件路径
        '''
        # 1 dict
        if isinstance(files, dict):
            for key, file in files.items():
                files[key] = read_file(file)
            return files

        # 2 str: 目录/文件转list
        if isinstance(files, str):
            path = files
            if not os.path.exists(path):
                raise Exception(f"config_from_files/secret_files动作参数[{path}]因是str类型而被认定为目录或文件，但目录或文件不存在")
            if os.path.isdir(path): # 目录
                files = [os.path.join(path, f) for f in os.listdir(path)]
            else: # 文件
                files = [path]

        # 3 list
        if isinstance(files, list):
            ret = {}
            for file in files:
                key = os.path.basename(file) # 文件名作为key
                ret[key] = read_file(file)
            return ret

        # 4 其他: 报错
        raise Exception(f"config_from_files/secret_files动作参数只接受dict/list/str类型，而实际参数是: {files}")

    # 生成配置
    def configmap(self):
        if self._config_data:
            yaml = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": self.build_metadata(),
                "data": self._config_data
            }
            self.save_yaml(yaml, '-config.yml')

    @replace_var_on_params
    def secret(self, data):
        '''
        生成密钥， 其实跟config差不多，只不过config是明文，secret是密文
        :param data 密钥项，可以是键值对，可以包含变量，如
              name: shigebeyond
              nginx.conf: ${read_file(./nginx.conf)}
              也可以是变量表达式，如 $cfg 或 ${read_yaml(./cfg.yml)}
        '''
        if not isinstance(data, dict):
            raise Exception('secret动作参数只接受dict类型')
        self._secret_data.update(data)

    @replace_var_on_params
    def secret_files(self, files):
        '''
        以文件内容的方式来设置配置，在挂载secret时items默认填充用secret_files()写入的key
        :param files 配置文件list或dict或目录
                  dict类型： key是配置项名，value是文件路径，如 nginx.conf: ./nginx.conf
                  list类型： 元素是文件路径，会用文件名作为key
                  str类型： 目录/文件路径
        '''
        files = self.build_config_data_from_files(files)
        self._secret_file_keys.extend(files.keys())
        self._secret_data.update(files)

    # 生成secret
    def secretmap(self):
        if self._secret_data:
            yaml = {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": self.build_metadata(),
                "type": "Opaque",
                "data": self._secret_data
            }
            self.save_yaml(yaml, '-secret.yml')

    # 修正有副本的选项
    def fix_replicas_option(self, option, action):
        if not option:
            return {
                "replicas": 1
            }
        if isinstance(option, (int, float, str)):
            return {
                "replicas": int(option)
            }
        if isinstance(option, dict):
            return option
        raise Exception(f"动作{action}的参数非字典类型")

    @replace_var_on_params
    def pod(self, option):
        '''
        生成pod
        '''
        if not option:
            option = {}
        yaml = self.build_pod_template(option)
        yaml.update({
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": self.build_metadata(),
        })
        self.save_yaml(yaml, '-pod.yml')

    @replace_var_on_params
    def rc(self, option):
        '''
        生成rc
        :param option 部署选项 {replicas}
                        replicas 副本数
        '''
        option = self.fix_replicas_option(option, 'rc')
        yaml = {
            "apiVersion": "v1",
            "kind": "ReplicationController",
            "metadata": self.build_metadata(),
            "spec": {
                "replicas": option.get("replicas", 1),
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option)
            }
        }

        self.save_yaml(yaml, '-rc.yml')

    @replace_var_on_params
    def rs(self, option):
        '''
        生成rs
        :param option 部署选项 {replicas}
                        replicas 副本数
        '''
        option = self.fix_replicas_option(option, 'rs')
        yaml = {
            "apiVersion": "v1",
            "kind": "ReplicaSet",
            "metadata": self.build_metadata(),
            "spec": {
                "replicas": option.get("replicas", 1),
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option)
            }
        }

        self.save_yaml(yaml, '-rs.yml')

    @replace_var_on_params
    def ds(self, option):
        '''
        生成ds：每个node运行一个pod，因此不需要 replicas 选项
        :params option 部署选项 {nodeSelector}
                        nodeSelector 节点选择，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'ds')
        yaml = {
            "apiVersion": "v1",
            "kind": "DaemonSet",
            "metadata": self.build_metadata(),
            "spec": {
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option)
            }
        }

        self.save_yaml(yaml, '-rs.yml')

    @replace_var_on_params
    def sts(self, option):
        '''
        生成 StatefulSet
        :params option 部署选项 {replicas, nodeSelector}
                        replicas 副本数
                        nodeSelector 节点选择，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'sts')
        yaml = {
            "apiVersion": "v1",
            "kind": "StatefulSet",
            "metadata": self.build_metadata(),
            "spec": {
                "replicas": option.get("replicas", 1),
                "serviceName": self._app,
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option)
            }
        }

        self.save_yaml(yaml, '-sts.yml')

        self._is_sts = True

    @replace_var_on_params
    def deploy(self, option):
        '''
        生成部署
        :params option 部署选项 {replicas, nodeSelector}
                        replicas 副本数
                        nodeSelector 节点选择，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'deploy')
        yaml = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": self.build_metadata(),
            "spec": {
                "replicas": option.get("replicas", 1),
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option)
            }
        }
        self.save_yaml(yaml, '-deploy.yml')

    @replace_var_on_params
    def job(self, option):
        '''
        生成job(批处理任务)
        :params option 部署选项 {completions, parallelism, activeDeadlineSeconds, backoffLimit, ttlSecondsAfterFinished, nodeSelector}
                        completions 标志Job结束需要成功运行的Pod个数
                        parallelism 标志并行运行的Pod的个数
                        activeDeadlineSeconds 表示 Pod 可以运行的最长时间，达到设置的该值后，Pod 会自动停止，优先于 backoffLimit
                        backoffLimit 最大允许失败的次数
                        ttlSecondsAfterFinished 任务完成后的n秒后自动删除pod
                        nodeSelector 节点选择，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        yaml = {
            "apiVersion": "apps/v1",
            "kind": "Job",
            "metadata": self.build_metadata(),
            **self.build_job(option)
        }
        self.save_yaml(yaml, '-job.yml')

    def build_job(self, option):
        job = {
            "completions": option.get("completions"),
            "parallelism": option.get("parallelism"),
            "activeDeadlineSeconds": option.get("activeDeadlineSeconds"),
            "backoffLimit": option.get("backoffLimit"),
            "ttlSecondsAfterFinished": option.get("ttlSecondsAfterFinished"),
            "spec": {
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option, restartPolicy="Never") # pod启动失败时不会重启，而是通过job-controller重新创建pod供节点调度。
            }
        }
        del_dict_none_item(job)
        return job

    @replace_var_on_params
    def cronjob(self, option):
        '''
        生成定时job
          参考 https://blog.csdn.net/u012711937/article/details/124478596
        :params option 部署选项 {successfulJobsHistoryLimit, failedJobsHistoryLimit, startingDeadlineSeconds, nodeSelector}
                        schedule cron表达式，格式为 Minutes Hours DayofMonth Month DayofWeek Year，即分 小时 日 月 周，其中?与*都是表示给定字段是任意值
                        startingDeadlineSeconds 过了调度时间n秒后没有启动成功，就不再启动
                        concurrencyPolicy 并发策略： Allow 允许job并发执行，Forbid只允许当前这个执行，Replace取消当前这个，而执行新的
                        suspend: 是否挂起，如果设置为true，后续所有执行被挂起
                        successfulJobsHistoryLimit 保留几个成功的历史记录
                        failedJobsHistoryLimit 保留几个失败的历史记录
                        ttlSecondsAfterFinished 任务完成后的n秒后自动删除pod，有该选项，则successfulJobsHistoryLimit和failedJobsHistoryLimit会失效，反正不管成功或失败，到点照样删
                        nodeSelector 节点选择，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
                        ...其他参数参考 job()
        '''
        spec = {
            "schedule": option["schedule"],
            "startingDeadlineSeconds": option.get("startingDeadlineSeconds", 300),
            "concurrencyPolicy": option.get("concurrencyPolicy", "Allow"),
            "suspend": option.get("suspend", False),
            "successfulJobsHistoryLimit": option.get("successfulJobsHistoryLimit", 1),
            "failedJobsHistoryLimit": option.get("failedJobsHistoryLimit", 1),
            "selector": self.build_selector(option.get("selector")),
            "jobTemplate": self.build_job(option)
        }
        del_dict_none_item(spec)
        yaml = {
            "apiVersion": "apps/v1",
            "kind": "CronJob",
            "metadata": self.build_metadata(),
            "spec": spec
        }
        self.save_yaml(yaml, '-cronjob.yml')

    @replace_var_on_params
    def hpa(self, option):
        '''
        生成hpa
        :param option hpa选项 { deployment子动作, by }
                      deployment子动作，参考 deploy()，其中参数中的 replicas 是包含最小与最大值，如 1~10
                      by 扩容的度量指标，如 'cpu': 80, 'memory': 80
        '''
        # 扩容的度量指标
        by = get_and_del_dict_item(option, 'by')
        # 应该只剩下一个key，是关于deployment的子动作
        action = get_dict_first_key(option)
        params = option[action]
        replicas = params['replicas'].split('~', 1) # 最小值 + 最大值
        params['replicas'] = int(replicas[0]) # deployment子动作只需要最小值
        # 调用deployment子动作
        func = getattr(self, action)
        func(params)
        # 拼接 hpa
        yaml = {
            "apiVersion": "autoscaling/v2",
            "kind": "HorizontalPodAutoscaler",
            "metadata": self.build_metadata(),
            "spec": {
                "minReplicas": int(replicas[0]), # 最小副本数
                "maxReplicas": int(replicas[1]), # 最大副本数
                "scaleTargetRef": { # 绑定 deployment
                    "apiVersion": "apps/v1",
                    "kind": self.fullnames[action],
                    "name": self._app
                },
                "metrics": self.build_hpa_metrics(by) # 扩容的度量指标
            }
        }
        self.save_yaml(yaml, '-hpa.yml')

    # 构建扩容的度量指标
    def build_hpa_metrics(self, by):
        ret = []
        for key, val in by.items():
            # key: cpu/memory
            # val: 以%结尾则表示是使用率(百分比)，否则是使用量(绝对值)
            if val.endswith('%'): # 使用率
                target = {
                    "type": "Utilization",
                    "averageUtilization": float(val[:-1])
                }
            else: # 使用量
                target = {
                    "type": "AverageValue",
                    "averageValue": val
                }
            met = {
                "type": "Resource",
                "resource": {
                    "name": key,
                    "target": target
                }
            }
            ret.append(met)
        return ret

    def build_tolerations(self, tolerations):
        '''
        构建容忍
        :params tolerations 多行，格式为
                    :NoExecute
                     CriticalAddonsOnly
                     node.kubernetes.io/disk-pressure:NoSchedule
                     node-role.kubernetes.io/master=???:NoSchedule
        '''
        if tolerations is None or len(tolerations) == 0:
            return None
        if isinstance(tolerations, str):
            tolerations = [tolerations]

        ret = []
        for toleration in tolerations:
            item = {}
            # 解析 effect
            if ':' in toleration:
                toleration, effect = toleration.split(':')
                item['effect'] = effect
            if toleration:
                if '=' in toleration:
                    item['key'], item['vale'] = toleration.split('=')
                    item['operator'] = 'Equal'
                else:
                    item['key'] = toleration
                    item['operator'] = 'Exists'

            ret.append(item)
        return ret

    def build_pod_template(self, option, restartPolicy = "Always"):
        '''
        构建pod模板
        :param option {nodeSelector, tolerations}
                      nodeSelector 节点选择，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
                      nodeAffinity 节点亲和性，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
                      podAffinity pod亲和性，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
                      tolerations 容忍
                      activeDeadlineSeconds 表示 Pod 可以运行的最长时间，达到设置的该值后，Pod 会自动停止。
        :param restartPolicy 重启策略，默认为Always，对job为Never
        :return
        '''
        spec = {
            "activeDeadlineSeconds": option.get('activeDeadlineSeconds'),
            "initContainers": self._init_containers,
            "containers": self._containers,
            "restartPolicy": restartPolicy,
            "volumes": list(self._volumes.values()),
            "nodeSelector": option.get('nodeSelector'),
            "affinity": self.build_affinities(option.get('nodeAffinity'), option.get('podAffinity'), option.get('podAntiAffinity')),
            "tolerations": self.build_tolerations(option.get('tolerations')),
        }
        del_dict_none_item(spec)
        # 处理hostNetwork，要加上dnsPolicy
        host_network = option.get('hostNetwork', False)
        if host_network:
            spec["hostNetwork"] = host_network
            spec["dnsPolicy"] = option.get("dnsPolicy", "ClusterFirstWithHostNet") # 使用k8s DNS内部域名解析，如果不加，pod默认使用所在宿主主机使用的DNS，这样会导致容器内不能通过service name访问k8s集群中其他POD
        ret = {
            "metadata": {
                "labels": self.build_labels()
            },
            "spec": spec
        }
        return ret

    # 修正字典树的路径
    def fix_trie_paths(self, trie, path='', ret={}):
        # 遍历字典树的每个键
        for key, value in trie.items():
            # 构建当前节点的路径
            current_path = path + key
            if isinstance(value, dict): # 如果当前节点仍然是字典类型，则进行递归
                self.fix_trie_paths(value, current_path, ret)
            else: # 如果当前节点是叶子节点，则将完整路径添加到结果列表中
                ret[current_path] = value
        return ret

    @replace_var_on_params
    def ingress(self, url2port):
        '''
        生成 ingress
        :param url2port: url对容器端口的映射，dict类型，支持字典树形式
        :return:
        '''
        # 修正字典树的路径
        url2port = self.fix_trie_paths(url2port)
        # 按域名分组
        def select_scheme_host(url):
            url = parse.urlparse(url)
            return url.scheme + ':' + url.netloc
        host2urls = groupby(url2port.keys(), key=select_scheme_host)

        # 两层遍历：1 域名 2 url
        rules = [] # ingress转发规则
        tls_hosts = [] # http协议的主机
        # 遍历域名分组
        for scheme_host, urls in host2urls:
            # 遍历某域名下的url，生成转发规则
            paths = []
            for url, container_port in url2port.items():
                container_port = url2port[url]
                url = parse.urlparse(url)
                path = {
                    "path": url.path,
                    "backend": {  # 转发给哪个服务
                        "serviceName": self.get_service_name_by_port(container_port),
                        "servicePort": container_port
                    }
                }
                paths.append(path)
            # 生成该域名的规则
            scheme, host = scheme_host.split(':')
            rule = {
                "host": host,
                scheme: {
                    "paths": paths
                }
            }
            rules.append(rule)
            # 记录https的域名
            if scheme == 'https':
                tls_hosts.append(host)

        if tls_hosts:
            tls_hosts = [
                {
                    "hosts": tls_hosts,
                    "secretName": self._app + '-tls' # 约定好 tls secret 名字
                }
            ]

        yaml = {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": self.build_metadata(),
            "spec": {
                "tls": tls_hosts,
                "rules": rules
            }
        }
        self.save_yaml(yaml, '-ingress.yml')

    def service(self):
        '''
        根据 containers 中的映射路径来生成service
           映射路径如： 宿主机端口:服务端口:容器端口
        '''
        if len(self._ports) == 0:
            return
        yamls = []
        for type, ports in self.build_service_type2ports():
            yaml = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": self.build_metadata('-svc-' + type.lower()),
                "spec": {
                    "type": type,
                    "ports": list(ports),
                    "selector": self.build_labels()
                },
                "status":{
                    "loadBalancer": {}
                }
            }
            # statefulset 要使用 headless service
            if self._is_sts and type == 'ClusterIP':
                yaml["spec"]["clusterIP"] = None # HeadLess service
            yamls.append(yaml)
        self.save_yaml(yamls, '-svc.yml')

    def get_service_name_by_port(self, container_port):
        for port in self._ports:
            port_map = self.build_service_port(port)
            if port_map["targetPort"] == container_port:
                return self._app + '-svc-' + port_map["type"].lower()

        raise Exception(f"找不到端口[{container_port}]对应的服务类型")

    def build_service_type2ports(self):
        '''
        构建service需要的端口
        '''
        port_maps = []
        for port in self._ports:
            port_map = self.build_service_port(port)
            port_maps.append(port_map)

        # 按类型分组
        def select_type(x):
            type = x['type']
            del x['type']
            return type
        self._service_type2ports = groupby(port_maps, key=select_type)
        return self._service_type2ports

    def build_service_port(self, port):
        # 解析协议
        protocol = "TCP"
        if "://" in port:
            protocol, port = port.split("://", 1)
            protocol = protocol.upper()
        # 解析1~3个端口
        parts = list(map(int, port.split(':')))  # 分割+转int
        n = len(parts)
        if n == 3:
            return {
                "type": "NodePort",  # 仅用于分组，生成yaml前要删掉
                "name": "p" + str(parts[2]),
                "nodePort": parts[0],  # 宿主机端口
                "port": parts[1],  # 服务端口
                "targetPort": parts[2],  # 容器端口
                "protocol": protocol
            }

        if n == 2:
            return {
                "type": "ClusterIP",  # 仅用于分组，生成yaml前要删掉
                "name": "p" + str(parts[1]),
                "port": parts[0],  # 服务端口
                "targetPort": parts[1],  # 容器端口
                "protocol": protocol
            }

        return {
            "type": "ClusterIP",  # 仅用于分组，生成yaml前要删掉
            "name": "p" + port,
            "port": port,  # 服务端口
            "targetPort": port,  # 容器端口
            "protocol": protocol
        }

    @replace_var_on_params
    def initContainers(self, containers):
        if containers:
            self._init_containers = [self.build_container(name, option) for name, option in containers.items()]

    # 不使用 @replace_var_on_params：containers的选项中如果存在用ref_resource_field()的变量表达式来注入env时，而ref_resource_field()是依赖当前容器名，而containers()没执行前是不知道的，因此不能提前替换变量(执行@replace_var_on_params)
    def containers(self, containers):
        self._containers = [self.build_container(name, option) for name, option in containers.items()]

    # 构建容器
    def build_container(self, name, option):
        # 拿到容器名，才能对option替换变量
        self._curr_container = name
        option = replace_var(option, False)
        ret = {
            "name": name,
            "image": get_and_del_dict_item(option, 'image'),
            "imagePullPolicy": get_and_del_dict_item(option, 'imagePullPolicy', "IfNotPresent"),
            "env": self.build_env(get_and_del_dict_item(option, 'env')),
            "envFrom": self.build_env_from(get_and_del_dict_item(option, 'env_from')),
            "command": self.fix_command(get_and_del_dict_item(option, 'command')),
            "lifecycle": self.build_lifecycle(get_and_del_dict_item(option, 'postStart'), get_and_del_dict_item(option, 'preStop')),
            "ports": self.build_container_ports(get_and_del_dict_item(option, 'ports')),
            "resources": self.build_resources(get_and_del_dict_item(option, "resources")),
            "volumeMounts": self.build_volume_mounts(get_and_del_dict_item(option, "volumes")),
            "livenessProbe": self.build_probe(get_and_del_dict_item(option, "live?")),
            "readinessProbe": self.build_probe(get_and_del_dict_item(option, "ready?")),
        }
        ret.update(option)
        del_dict_none_item(ret)
        return ret

    def build_metadata(self, postfix = ''):
        meta = {
            "name": self._app + postfix,
            "labels": self.build_labels()
        }
        if self._ns:
            meta['namespace'] = self._ns
        return meta

    def build_labels(self, lbs = None):
        if not lbs:
            return self._labels
        # 合并标签
        return dict(lbs, **self._labels)

    def build_selector(self, matches, for_deploy = True):
        '''
        构建 label selector
        :param matches 标签选择的表达式
                     dict 类型，走matchLabels，如 "kubernetes.io/os": "linux" 或 "disk": "ssd"
                     list 类型，解析每项表达式，走matchExpressions， 如 - Tier in [backend]，操作符有In/NotIn/Exists/DoesNotExist/Gt/Lt
        :param for_deploy 是否用于rc/rs/deploy/job级别，否则用于affinity
        '''
        # dict
        if matches is None or isinstance(matches, dict):
            return {
                "matchLabels": self.build_labels(matches)
            }

        # list：逐个解析表达式
        lables = {}
        exprs = []
        for mat in matches:
            if '=' in mat: # 相等：走 matchLabels
                key, val = re.split('\s*=\s*', mat)
                lables[key] = val
            else: # 其他: In/NotIn/Exists/DoesNotExist/Gt/Lt，走 matchExpressions
                expr = self.build_match_exp(mat)
                exprs.append(expr)
        ret = {}
        if for_deploy: # rc/rs/deploy/job
            ret["matchLabels"] = self.build_labels(lables) # 带app标签，用于给rc/rs/deploy/job过滤pod
        elif lables:
            ret["matchLabels"] = lables
        if exprs:
            ret["matchExpressions"] = exprs
        return ret

    def build_match_exp(self, mat):
        '''
        解析单个matchExpression
        :param mat 标签选择表达式，如 Tier in [backend]，操作符有In/NotIn/Exists/DoesNotExist/Gt/Lt
        '''
        parts = re.split('\s+', mat)
        key = parts[0]
        op = parts[1].lower()
        expr = {
            'key': key,
            'operator': op.capitalize()
        }
        if op == 'in' or op == 'notin':
            expr['values'] = parts[2].split(',')
        if op == 'gt' or op == 'lt':
            expr['values'] = float(parts[2])
        return expr

    def build_affinities(self, node_affinity, pod_affinity, pod_anti_affinity):
        ret = {
            "nodeAffinity": self.build_node_affinity(node_affinity),
            "podAffinity": self.build_pod_affinity(pod_affinity),
            "podAntiAffinity": self.build_pod_affinity(pod_anti_affinity),
        }
        del_dict_none_item(ret)
        return ret or None

    def build_node_affinity(self, option):
        '''
        构建节点亲和性
        :param option {require, prefer, weight}
                    require Node硬亲和性
                    prefer Node软亲和性
                    weight 单个规则的权重，仅用于Node软亲和性
        '''
        if not option:
            return None
        ret = {}
        # Node硬亲和性：节点必须满足规则才能调度
        if 'require' in option:
            ret["requiredDuringSchedulingIgnoredDuringExecution"] = {
                "nodeSelectorTerms": [
                    self.build_selector(option['require'], False)
                ]
            }

        # Node软亲和性：节点优先满足规则就调度，不强求
        if 'prefer' in option:
            ret["preferredDuringSchedulingIgnoredDuringExecution"] = [
                {
                    "preference": self.build_selector(option['prefer'], False),
                    "weight": option.get('weight', 1)
                }
              ]

        return ret

    def build_pod_affinity(self, option):
        '''
        构建pod亲和性
        :param option {require, prefer, weight}
                    require requiredDuringSchedulingIgnoredDuringExecution简写，pod硬亲和性
                    prefer preferredDuringSchedulingIgnoredDuringExecution简写，pod软亲和性
                    weight 单个规则的权重，仅用于pod软亲和性
                    tkey topologyKey简写，表示节点所属的 topology 范围，可省，默认为 kubernetes.io/hostname
        '''
        if not option:
            return None
        ret = {}
        # pod硬亲和性：pod必须满足规则才能调度
        if 'require' in option:
            ret["requiredDuringSchedulingIgnoredDuringExecution"] = [
                {
                    "labelSelector": self.build_selector(option['require'], False),
                    "topologyKey": option.get('topologyKey') or option.get('tkey') or 'kubernetes.io/hostname'
                }
            ]

        # pod软亲和性：pod优先满足规则就调度，不强求
        if 'prefer' in option:
            ret["preferredDuringSchedulingIgnoredDuringExecution"] = [
                {
                  "weight": option.get('weight', 1),
                  "podAffinityTerm": {
                        "labelSelector": self.build_selector(option['prefer'], False),
                        "topologyKey": option.get('topologyKey') or option.get('tkey') or 'kubernetes.io/hostname'
                    }
                }
              ]

        return ret

    def build_lifecycle(self, postStart, preStop):
        ret = {}
        if postStart:
            ret['postStart'] = {
                "exec": {
                    "command": self.fix_command(postStart)
                }
            }
        if preStop:
            ret['preStop'] = {
                "exec": {
                    "command": self.fix_command(preStop)
                }
            }
        if ret:
            return ret
        return None

    def build_command(self, cmd):
        if isinstance(cmd, str):
            return re.split('\s+', cmd) # 空格分割
        return cmd

    def build_container_ports(self, ports):
        '''
        构建容器端口
        :param ports 多行，格式为 宿主机端口:服务端口:容器端口
        '''
        if ports is None or len(ports) == 0:
            return None
        # ports的格式是多行的： 宿主机端口:服务端口:容器端口
        if isinstance(ports, str):
            ports = [ports]
        # 记录每个容器的端口映射
        self._ports.extend(ports)
        # 解析容器端口
        container_ports = []
        for port in ports:
            port = port.rsplit(':', 1)[-1]
            container_ports.append({
                "containerPort": int(port)
            })
        return container_ports

    def build_resources(self, option):
        '''
        构建资源
        :param option
            {
                "cpus": '0.01~0.1', # 最小值~最大值
                "memory": "100Mi~200Mi"
            },
        '''
        if option is None or len(option) == 0:
            return None
        # 分割最小值与最大值
        cpus = self.split_resource_span(option.get("cpu"))
        mems = self.split_resource_span(option.get("memory"))
        # 最小值
        ret = {
            "requests": self.build_resource_item(get_list_item(cpus, 0), get_list_item(mems, 0))
        }
        # 最大值
        if len(cpus) > 1 or len(mems) > 1:
            ret["limits"] = self.build_resource_item(get_list_item(cpus, 1), get_list_item(mems, 1))
        return ret

    def split_resource_span(self, span):
        '''
        分解资源的最小值与最大值
        :param span: 资源范围表达式： 最小值~最大值
        :return: 要返回list
        '''
        if not span:
            return []

        if isinstance(span, (list, set)):
            return span

        if isinstance(span, str):
            return span.split('~', 1)

        return [span]

    def build_resource_item(self, cpu, mem):
        ret = {}
        if cpu:
            ret["cpu"] = cpu
        if mem:
            ret["memory"] = mem
        return ret

    # 构建卷
    # https://blog.csdn.net/weixin_43849415/article/details/108630142
    # https://www.cnblogs.com/RRecal/p/15699245.html
    def build_volume(self, protocol, host, host_path):
        # 临时目录
        if protocol == 'emptyDir':
            return {
                "emptyDir": {}
            }
        # 本地文件
        if protocol == 'file':
            return {
                'hostPath': {
                  'path': host_path,
                  'type': 'FileOrCreate'
                }
            }
        # 本地目录
        if protocol == 'dir':
            return {
                'hostPath': {
                  'path': host_path,
                  'type': 'DirectoryOrCreate'
                }
            }
        # nfs
        if protocol == 'nfs':
            return {
                'nfs': {
                    'server': host,
                    'path': host_path,
                }
            }
        # configmap: https://blog.csdn.net/weixin_45880055/article/details/117590045
        if protocol == 'config':
            return {
                'configMap': {
                    'name': self._app,
                    # 指定items(配置挂载的key)
                    # items的作用是 1指定key挂载到不同名到文件上 2过滤要挂载的key，否则挂载全部key
                    # self._config_file_keys默认挂载的文件类型的key，仅当host_path没指定时用到
                    'items': self.build_config_volume_items(host_path or self._config_file_keys)
                }
            }
        # secret: https://www.cnblogs.com/litzhiai/p/11950273.html
        if protocol == 'secret':
            return {
                'secret': {
                    'secretName': host_path,
                    'items': self.build_config_volume_items(host_path or self._secret_file_keys) # 跟config实现一样
                }
            }
        # downwardAPI: https://blog.csdn.net/ens160/article/details/124446138
        if protocol == 'downwardAPI':
            return {
                "downwardAPI": {
                    "items": self.build_downwardapi_volume_items(host_path)
                }
            }
        raise Exception(f'暂不支持卷协议: {protocol}')

    def build_downwardapi_volume_items(self, host_path):
        '''
        指定items(downwardAPI挂载的key)
        items的作用是 1指定key挂载到不同名到文件上 2过滤要挂载的key，否则挂载全部key
        :param: host_path 对应要挂载的key，只接受 labels / annotations
        '''
        # 1 key
        # 没指定key，则挂载所有key
        keys = ['labels', 'annotations']
        if host_path: # 有指定key
            if host_path not in keys:
                raise Exception(f"downwardAPI协议的挂载key只接受{keys}")
            keys = [host_path]

        # 2 根据key拼接item
        return [{
                    "path": key,
                    "fieldRef": {
                        "fieldPath": f"metadata.{key}"
                    }
                } for key in keys]


    def build_config_volume_items(self, keys):
        '''
        指定items(配置挂载的key)
        items的作用是 1指定key挂载到不同名到文件上 2过滤要挂载的key，否则挂载全部key
        self._config_file_keys或self._secret_file_keys默认挂载的文件类型的key，仅当host_path没指定时用到
        :param: host_path 对应要挂载的key
        :param: config_file_keys 默认挂载的文件类型的key，仅当host_path没指定时用到
        '''
        # 1 没指定key，则挂载所有key，跳过指定items
        if not keys:
            return

        # 2 有指定key
        # 2.1 根据key拼接item
        if isinstance(keys, str): # 单个key
            keys = [keys]
        return [{'key': key, 'path': key} for key in keys]

    def build_volume_mounts(self, mounts):
        '''
        构建卷映射
        :params mounts 多行，格式为
                    /var/log/nginx -- 无协议+无本地卷映射，临时目录 emptyDir
                    emptyDir://:/var/log/nginx -- 挂载临时目录 emptyDir，如果你要换卷名，可以在emptyDir://后加数字(没啥用，只是为了区分卷名)，如emptyDir://1:/var/log/nginx
                    /lnmp/www/:/www -- 无协议+有本地卷映射，挂载目录或文件(k8s自动识别)
                    dir:///apps/fpm729/etc/php-fpm/:/usr/local/etc/php-fpm.d/:rw -- 挂载目录
                    file:///var/run/docker.sock:/var/run/docker.sock:ro -- 挂载文件，只读
                    nfs://192.168.159.14/data:/mnt -- 挂载nfs
                    config://:/etc/nginx/conf.d -- 将configmap挂载为目录
                    config://nginx.conf:/etc/nginx/nginx.conf -- 将configmap中key=nginx.conf的单个配置项挂载为文件，不同的key写不同的行
                    downwardAPI://:/etc/podinfo -- 将元数据labels和annotations以文件的形式挂载到目录
                    downwardAPI://labels:/etc/podinfo/labels.properties -- 将元数据labels挂载为文件
                    其中生成的卷名为 vol-md5(最后一个:之前的部分)
        '''
        if mounts is None or len(mounts) == 0:
            return None
        if isinstance(mounts, str):
            mounts = [mounts]

        ret = []
        for mount in mounts:
            # 1 解析末尾的 rw ro
            ro = False # 是否只读
            mat = re.search(':r[wo]', mount)
            if mat is not None:
                ro = mat.group(0) == ':ro'

            # 2 解析协议：协议格式参考函数注释
            if ':' not in mount:
                mount = f"emptyDir://:{mount}"
            protocol = None
            if "://" in mount: # 有协议
                #  mat = re.search('(\w+)://([\w\d\._]*)(/.+):(.+)', mount)
                mat = re.search('(\w+)://(.*?):(.+)', mount)
                protocol = mat.group(1) # 协议
                host_and_path = mat.group(2) # 主机 + 宿主机路径
                mount_path = mat.group(3) # 容器中挂载路径
                mat = re.search('([\w\d\._:]+)(/.+)', host_and_path)
                if mat:
                    host = mat.group(1) # 主机
                    host_path = mat.group(2) # 宿主机路径
                else:
                    host = ''
                    host_path = host_and_path
                vol = self.build_volume(protocol, host, host_path)
            elif ':' in mount: # 无协议+有本地卷映射，如 /lnmp/www/:/www
                host_path, mount_path = mount.split(':', 1)
                vol = {
                    'hostPath': {
                      'path': host_path,
                    }
                }
            else:
                raise Exception(f'无法识别卷映射路径: {mount}')

            # 3 记录挂载
            # 卷名为 vol-md5(最后一个:之前的部分)
            # name = 'vol-' + random_str(10)
            # name = 'vol-' + md5(mount_path)
            name = mount.rsplit(':', 1)[0]
            name = 'vol-' + md5(name)
            yaml = {
                "name": name,
                "mountPath": mount_path
            }
            # 一般configmap/secret/downwardAPI要挂载为目录，但如果指定了 host_path 表示只挂载单个key为文件
            # host_path为key，mountPath为挂载的容器文件路径
            if (protocol == 'config' or protocol == 'secret' or protocol == 'downwardAPI') and host_path:
                yaml['subPath'] = host_path
            # 只读
            if ro:
                yaml['readOnly'] = True
            ret.append(yaml)

            # 4 记录卷
            vol["name"] = name
            self._volumes[name] = vol # 用name来去重
        return ret

    def build_probe(self, option):
        '''
        构建probe： livenessProbe/readinessProbe
        probe文档：https://blog.csdn.net/Jerry00713/article/details/123894868
        probe的go实现： https://vimsky.com/examples/detail/golang-ex-k8s.io.kubernetes.pkg.api-Probe-HTTPGet-method.html
        '''
        if not option:
            return None

        # 处理各种秒数参数，支持简写 i:initialDelaySeconds, p:periodSeconds, t:timeoutSeconds, s:successThreshold, f:failureThreshold，
        seconds = self.build_probe_seconds(option['seconds'])
        # 处理action参数
        action = self.build_probe_action(option['action'])
        # 合并参数
        return dict(seconds, **action)

    # probe的各种秒数参数名的简写映射
    probe_second_field_short_map = {
        'i': 'initialDelaySeconds',
        'p': 'periodSeconds',
        't': 'timeoutSeconds',
        's': 'successThreshold',
        'f': 'failureThreshold'
    }

    def build_probe_seconds(self, seconds):
        '''
        处理probe的各种秒数参数，支持简写，简写规则是用全写的首字母，具体映射如下 i:initialDelaySeconds, p:periodSeconds, t:timeoutSeconds, s:successThreshold, f:failureThreshold
        1 支持dict类型参数
        1.1 全写 {initialDelaySeconds: 5, periodSeconds: 5, timeoutSeconds: 5, successThreshold: 1, failureThreshold: 5}
        1.2 简写 {i: 5, p: 5, t: 5, s: 1, f: 5}
        2 支持str类型参数
        2.1 全写 initialDelaySeconds=5 periodSeconds=5 timeoutSeconds=5 successThreshold=1 failureThreshold=5
        2.2 简写 i=5 p=5 t=5 s=1 f=5
        '''
        # str转dict
        if isinstance(seconds, str):
            seconds = seconds.strip().replace(' ', '&') # 转为url的query string格式
            seconds = dict(parse.parse_qsl(seconds)) # 解析为dict
        # 处理dict中的简写，简写=用全写的首字母，要恢复全写
        ret = {}
        for k, v in seconds.items():
            if k in self.probe_second_field_short_map:
                k = self.probe_second_field_short_map[k]
            ret[k] = int(v)
        return ret

    def build_probe_action(self, action):
        ''''
        构建probe的action
        :param: action 动作
                无协议：执行命令，如
                http协议：
                tcp协议
        '''
        # 1 无协议: 执行命令
        if "://" not in action:
            if isinstance(action, str):
                action = self.fix_command(action)
            return {
                "exec": {
                    "command": action
                }
            }

        # 2 有协议：http或tcp
        # 解析headers：有 -h a=1&b=2
        headers = None
        if '-h' in action:
            action, headers = re.split('\s+-h\s+', action)
        # 解析url
        url = parse.urlparse(action)
        host_and_port = url.netloc
        if ':' in host_and_port:
            host, port = host_and_port.split(':')
            port = int(port)
        else:
            host = host_and_port
            port = 80
        # 2.1 tcp
        if url.scheme.lower() == 'tcp':
            return {
                'tcpSocket': {
                    'port': port
                }
            }
        # 2.2 http/https
        path = url.path or '/'
        if url.query:
            path = path + '?' + url.query
        ret= {
            'httpGet': {
                'scheme': url.scheme.upper(),
                'port': port,
                'path': path,
            }
        }
        # 有host
        if host != 'localhost' and host != '127.0.0.1':
            ret['httpGet']['host'] = host
        # 有请求头
        if headers:
            headers = parse.parse_qsl(headers)
            headers = [{'name': k, 'value': v[0]} for k, v in headers]
            ret['httpGet']['httpHeaders'] = headers
        return ret

    def fix_command(self, cmd):
        if isinstance(cmd, str):
            # return re.split('\s+', cmd)  # 空格分割
            return ["/bin/bash", "-c", cmd] # bash修饰
        return cmd

    def ref_pod_field(self, field):
        '''
        在给环境变量赋时值，注入Pod信息
          参考 https://blog.csdn.net/skh2015java/article/details/109229107
        :param field Pod信息字段，仅支持 metadata.name, metadata.namespace, metadata.uid, spec.nodeName, spec.serviceAccountName, status.hostIP, status.podIP, status.podIPs
        '''
        return {
            "fieldRef": {
                "fieldPath": field
            }
        }

    def ref_resource_field(self, field):
        '''
        在给环境变量赋值时，注入容器资源信息
          参考 https://blog.csdn.net/skh2015java/article/details/109229107
        :param field 容器资源信息字段，仅支持 requests.cpu, requests.memory, limits.cpu, limits.memory
        '''
        return {
            "resourceFieldRef": {
                "containerName": self._curr_container,
                "resource": field
            }
        }

    def ref_config(self, key):
        '''
        在给环境变量赋值时，注入配置信息
        :param key
        '''
        return {
            "configMapKeyRef":{
              "name": self._app, # The ConfigMap this value comes from.
              "key": key # The key to fetch.
            }
        }

    def ref_secret(self, key):
        '''
        在给环境变量赋值时，注入secret信息
        :param key
        '''
        return {
            "secretKeyRef":{
              "name": self._app, # The Secret this value comes from.
              "key": key # The key to fetch.
            }
        }

    # 构建容器中的环境变量
    def build_env(self, env):
        if env is None or len(env) == 0:
            return None

        ret = []
        for key, val in env.items():
            item = {
                "name": key,
            }
            if isinstance(val, str):
                item["value"] = val
            else:
                item["valueFrom"] = val
            ret.append(item)
        return ret

    def build_env_from(self, types):
        '''
        构建容器中的环境变量
           参考 https://blog.csdn.net/u012734723/article/details/122906200
        :param types 仅支持 config / secret
        '''
        if types is None or len(types) == 0:
            return None

        if isinstance(types, str):
            types = [types]
        ret = []
        for val in types:
            if isinstance(val, str):
                if val == 'config':
                    val = "configMapRef"
                else:
                    val = "secretRef"
                item = {
                    val: {
                        "name": self._app
                    }
                }
            ret.append(item)
        return ret

# cli入口
def main():
    # 基于yaml的执行器
    boot = Boot()
    # 读元数据：author/version/description
    dir = os.path.dirname(__file__)
    meta = read_init_file_meta(dir + os.sep + '__init__.py')
    # 步骤配置的yaml
    step_files, option = parse_cmd('K8sBoot', meta['version'])
    if len(step_files) == 0:
        raise Exception("Miss step config file or directory")
    try:
        # 执行yaml配置的步骤
        boot.run(step_files)
    except Exception as ex:
        log.error(f"Exception occurs: current step file is %s", boot.step_file, exc_info=ex)
        raise ex


if __name__ == '__main__':
    main()
    # data = read_yaml('/home/shi/code/k8s/k8s-demo-master/test.yaml')
    # print(json.dumps(data))
