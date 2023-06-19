#!/usr/bin/python3
# -*- coding: utf-8 -*-

import fnmatch
import hashlib
import json
import os
import re
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
            'config_files': self.config_files,
            'secret': self.secret,
            'secret_files': self.secret_files,
            'pod': self.pod,
            'rc': self.rc,
            'rs': self.rs,
            'ds': self.ds,
            'sts': self.sts,
            'deploy': self.deploy,
            'initContainers': self.initContainers,
            'containers': self.containers,
        }
        self.add_actions(actions)
        # 自定义函数
        funcs = {
            'from_field': self.from_field,
            'from_config': self.from_config,
            'from_secret': self.from_secret,
        }
        custom_funs.update(funcs)

        self._ns = '' # 命名空间
        self._app = '' # 应用名
        self._labels = {}  # 记录标签
        self._config_data = {} # 记录设置过的配置
        self._config_file_keys = [] # 记录文件类型的key
        self._secret_data = {} # 记录设置过的密文
        self._secret_file_keys = [] # 记录文件类型的key
        self._init_containers = [] # 记录处理过的初始容器
        self._containers = [] # 记录处理过的容器
        self._volumes = [] # 记录容器中的卷
        self._ports = [] # 记录容器中的端口映射
        self._is_sts = False # 是否用 statefulset 来部署

    # 清空app相关的属性
    def clear_app(self):
        self._app = ''  # 应用名
        self._labels = {}  # 记录标签
        self._config_data = {}  # 记录设置过的配置
        self._config_file_keys = [] # 记录文件类型的key
        self._secret_data = {}  # 记录设置过的密文
        self._secret_file_keys = [] # 记录文件类型的key
        self._init_containers = []  # 记录处理过的初始容器
        self._containers = []  # 记录处理过的容器
        self._volumes = []  # 记录容器中的卷
        self._ports = []  # 记录容器中的端口映射
        self._is_sts = False  # 是否用 statefulset 来部署

    # 保存yaml
    def save_yaml(self, data, file_postfix):
        # 创建目录
        if not os.path.exists(self._app):
            os.makedirs(self._app)
        # 保存文件
        file = os.path.join(self._app, self._app + file_postfix)
        write_file(file, yaml.dump(data))

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
    def config_files(self, files):
        '''
        以文件内容的方式来设置配置，在挂载configmap时items默认填充用config_files()写入的key
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
                raise Exception(f"config_files/secret_files动作参数[{path}]因是str类型而被认定为目录或文件，但目录或文件不存在")
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
        raise Exception(f"config_files/secret_files动作参数只接受dict/list/str类型，而实际参数是: {files}")

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
        if isinstance(option, (int, str)):
            return {
                "replicas": option
            }
        if isinstance(option, dict):
            return option
        raise Exception(f"动作{action}的参数非字典类型")

    @replace_var_on_params
    def pod(self, _):
        '''
        生成pod
        '''
        yaml = self.build_pod_template()
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
                "template": self.build_pod_template()
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
                "template": self.build_pod_template()
            }
        }

        self.save_yaml(yaml, '-rs.yml')

    @replace_var_on_params
    def ds(self, option):
        '''
        生成ds
        :params option 部署选项 {node}
                        node 节点选择，如 "beta.kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'ds')
        yaml = {
            "apiVersion": "v1",
            "kind": "DaemonSet",
            "metadata": self.build_metadata(),
            "spec": {
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option.get('nodes'), option.get('tolerations'))
            }
        }

        self.save_yaml(yaml, '-rs.yml')

    @replace_var_on_params
    def sts(self, option):
        '''
        生成 StatefulSet
        :params option 部署选项 {replicas, node}
                        replicas 副本数
                        node 节点选择，如 "beta.kubernetes.io/os": "linux" 或 "disk": "ssd"
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
                "template": self.build_pod_template()
            }
        }

        self.save_yaml(yaml, '-sts.yml')

        self._is_sts = True

    @replace_var_on_params
    def deploy(self, option):
        '''
        生成部署
        :params option 部署选项 {replicas, node}
                        replicas 副本数
                        node 节点选择，如 "beta.kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'deploy')
        yaml = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": self.build_metadata(),
            "spec": {
                "replicas": option.get("replicas", 1),
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option.get('nodes'), option.get('tolerations'))
            }
        }
        self.save_yaml(yaml, '-deploy.yml')

    @replace_var_on_params
    def job(self, option):
        '''
        生成job(批处理任务)
        :params option 部署选项 {completions, parallelism, activeDeadlineSeconds, node}
                        completions 标志Job结束需要成功运行的Pod个数，默认为1
                        parallelism 标志并行运行的Pod的个数，默认为1
                        activeDeadlineSeconds 标志失败Pod的重试最大时间，超过这个时间不会继续重试
                        node 节点选择，如 "beta.kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_completions_option(option, 'job')
        yaml = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": self.build_metadata(),
            "spec": {
                "completions": option.get("completions", 1),
                "parallelism": option.get("parallelism", 1),
                "activeDeadlineSeconds": option.get("activeDeadlineSeconds", 1),
                "selector": self.build_selector(option.get("selector")),
                "template": self.build_pod_template(option.get('nodes'), option.get('tolerations'))
            }
        }
        self.save_yaml(yaml, '-job.yml')

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

    def build_pod_template(self, nodes = None, tolerations = None):
        '''
        构建pod模板
        :param nodes: 只有deploy才有node过滤器
        :param tolerations: 只有deploy才有容忍
        :return:
        '''
        ret = {
            "metadata": {
                "labels": self.build_labels()
            },
            "spec": {
                "initContainers": self._init_containers,
                "containers": self._containers,
                "restartPolicy": "Always",
                "volumes": self._volumes
            }
        }
        # 只有deploy才有node过滤器
        if nodes:
            ret["spec"]["nodeSelector"] = nodes
        # 只有deploy才有容忍
        if tolerations:
            ret["spec"]["tolerations"] = self.build_tolerations(tolerations)
        return ret

    def service(self):
        '''
        根据 containers 中的映射路径来生成service
           映射路径如： 宿主机端口:服务端口:容器端口
        '''
        if len(self._ports) == 0:
            return
        port_maps = []
        type = 'ClusterIP'
        for port in self._ports:
            # 解析协议
            protocol = "TCP"
            if "://" in port:
                protocol, port = port.split("://", 1)
                protocol = protocol.upper()

            # 解析1~3个端口
            parts = list(map(int, port.split(':'))) # 分割+转int
            n = len(parts)
            if n == 3:
                type = 'NodePort'
                port_map = {
                    "name": "p" + str(parts[2]),
                    "nodePort": parts[0],  # 宿主机端口
                    "port": parts[1],  # 服务端口
                    "targetPort": parts[2],  # 容器端口
                    "protocol": protocol
                }
            elif n == 2:
                port_map = {
                    "name": "p" + str(parts[1]),
                    "port": parts[0],  # 服务端口
                    "targetPort": parts[1],  # 容器端口
                    "protocol": protocol
                }
            else:
                port_map = {
                    "name": "p" + port,
                    "port": port,  # 服务端口
                    "targetPort": port,  # 容器端口
                    "protocol": protocol
                }
            port_maps.append(port_map)
        yaml = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": self.build_metadata(),
            "spec": {
                "type": type,
                "ports": port_maps,
                "selector": self.build_labels()
            },
            "status":{
                "loadBalancer": {}
            }
        }
        # statefulset 要使用 headless service
        if self._is_sts:
            yaml["spec"]["type"] = 'ClusterIP'
            yaml["spec"]["clusterIP"] = None # HeadLess service

        self.save_yaml(yaml, '-svc.yml')

    @replace_var_on_params
    def initContainers(self, containers):
        self._init_containers = [self.build_container(name, option) for name, option in containers.items()]

    @replace_var_on_params
    def containers(self, containers):
        self._containers = [self.build_container(name, option) for name, option in containers.items()]

    # 构建容器
    def build_container(self, name, option):
        ret = {
            "name": name,
            "image": get_and_del_dict_item(option, 'image'),
            "imagePullPolicy": get_and_del_dict_item(option, 'imagePullPolicy', "IfNotPresent"),
            "env": self.build_env(get_and_del_dict_item(option, 'env')),
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

    def build_metadata(self):
        meta = {
            "name": self._app,
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

    def build_selector(self, matches):
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
            else: # 其他: In/NotIn/Exists/DoesNotExist，走 matchExpressions
                parts = re.split('\s+', mat)
                key = parts[0]
                op = parts[1]
                expr = {
                    'key': key,
                    'operator': op
                }
                if op == 'In' or op == 'NotIn':
                    expr['values'] = parts[3].split(',')
                exprs.append(expr)
        ret = {
            "matchLabels": self.build_labels(lables)
        }
        if exprs:
            ret["matchExpressions"] = exprs
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
        cpus = option.get("cpus", "").split('~', 1)
        mems = option.get("memory", "").split('~', 1)
        # 最小值
        ret = {
            "requests": self.build_resource_item(cpus[0], mems[0])
        }
        # 最大值
        if len(cpus) > 1 or len(mems) > 1:
            ret["limits"] = self.build_resource_item(get_list_item(cpus, 1), get_list_item(mems, 1))
        return ret

    def build_resource_item(self, cpu, mem):
        ret = {}
        if not cpu:
            ret["cpus"] = cpu
        if not mem:
            ret["memory"] = mem
        return ret

    # 构建卷
    # https://blog.csdn.net/weixin_43849415/article/details/108630142
    # https://www.cnblogs.com/RRecal/p/15699245.html
    def build_volume(self, protocol, host, host_path):
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
            ret = {
                'configMap': {
                    'name': self._app,
                }
            }
            # 指定items(配置挂载的key)
            # items的作用是 1指定key挂载到不同名到文件上 2过滤要挂载的key，否则挂载全部key
            # self._config_file_keys默认挂载的文件类型的key，仅当host_path没指定时用到
            self.build_config_volume_items(host_path or self._config_file_keys, ret)
            return ret
        # secret: https://www.cnblogs.com/litzhiai/p/11950273.html
        if protocol == 'secret':
            ret = {
                'secret': {
                    'secretName': host_path,
                }
            }
            # 跟config实现一样
            self.build_config_volume_items(host_path or self._secret_file_keys, ret)
            return ret
        raise Exception(f'暂不支持卷协议: {protocol}')

    def build_config_volume_items(self, keys, volume):
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
        items = [{'key': key, 'path': key} for key in keys]

        # 2.2 记录到第二层的 items 属性
        type = list(volume.keys())[0]
        volume[type]['items'] = items

    def build_volume_mounts(self, mounts):
        '''
        构建目录映射
        :params mounts 多行，格式为
                    dir:///apps/fpm729/etc/php-fpm/:/usr/local/etc/php-fpm.d/:rw -- 挂载目录
                    file:///var/run/docker.sock:/var/run/docker.sock:ro -- 挂载文件，只读
                    nfs://192.168.159.14/data:/mnt -- 挂载nfs
                    config://:/etc/nginx/conf.d -- 将configmap挂载为目录，如果有调用config_files()则挂载文件类型的key，否则挂载所有的key
                    config://nginx.conf:/etc/nginx/nginx.conf -- 将configmap中key=nginx.conf的单个配置项挂载为文件，不同的key写不同的行
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
            '''
            2 解析协议
            dir:///apps/fpm729/etc/php-fpm/:/usr/local/etc/php-fpm.d/:rw -- 挂载目录
            file:///var/run/docker.sock:/var/run/docker.sock:ro -- 挂载文件，只读
            /lnmp/www/:/www -- 挂载目录或文件
            nfs://192.168.159.14/data:/mnt -- 挂载nfs
            config://:/etc/nginx/conf.d -- 将configmap挂载为目录
            config://nginx.conf:/etc/nginx/nginx.conf -- 将configmap中key=nginx.conf的单个配置项挂载为文件，不同的key写不同的行
            '''
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
            elif ':' in mount: # 无协议，有本地卷映射，如 /lnmp/www/:/www
                host_path, mount_path = mount.split(':', 1)
                vol = {
                    'hostPath': {
                      'path': host_path,
                    }
                }
            else:
                mount_path = mount
                vol = {
                    "emptyDir": {}
                }

            # 3 记录挂载
            # name = 'vol-' + random_str(10)
            name = 'vol-' + md5(mount_path)
            yaml = {
                "name": name,
                "mountPath": mount_path
            }
            # 一般configmap/secret要挂载为目录，但如果指定了 host_path 表示只挂载单个key为文件
            # host_path为key，mountPath为挂载的容器文件路径
            if (protocol == 'config' or protocol == 'secret') and host_path:
                yaml['subPath'] = host_path
            # 只读
            if ro:
                yaml['readOnly'] = True
            ret.append(yaml)

            # 4 记录卷
            vol["name"] = name
            self._volumes.append(vol)
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
        if url.scheme == 'tcp':
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

    # 用在变量赋值中的函数
    def from_field(self, field):
        return {
            "valueFrom": {
                "fieldRef": {
                    "fieldPath": field
                }
            }
        }

    # 用在变量赋值中的函数
    def from_config(self, key):
        return {
            "valueFrom":{
                "configMapKeyRef":{
                  "name": self._app, # The ConfigMap this value comes from.
                  "key": key # The key to fetch.
                }
            }
        }

    # 用在变量赋值中的函数
    def from_secret(self, key):
        return {
            "valueFrom":{
                "secretKeyRef":{
                  "name": self._app, # The Secret this value comes from.
                  "key": key # The key to fetch.
                }
            }
        }

    # 构建容器中的环境变量
    def build_env(self, env):
        if env is None or len(env) == 0:
            return None

        ret = []
        for key, val in env.items():
            ret.append({
                "name": key,
                "value": val
            })
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
    # data = read_yaml('/home/shi/code/k8s/k8s-demo-master/test-k8s/yaml/configmap/secret.yaml')
    # print(json.dumps(data))
