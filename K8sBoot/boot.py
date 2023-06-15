#!/usr/bin/python3
# -*- coding: utf-8 -*-

import fnmatch
import hashlib
import json
import os
from pyutilb.util import *
from pyutilb.file import *
from pyutilb.cmd import *
from pyutilb import YamlBoot, BreakException
from pyutilb.log import log
import platform

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
            'config': self.config,
            'secret': self.secret,
            'rc': self.rc,
            'rs': self.rs,
            'ds': self.ds,
            'sts': self.sts,
            'deploy': self.deploy,
            'service': self.service,
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
        self._containers = [] # 记录处理过的容器
        self._volumes = [] # 记录容器中的卷
        self._ports = [] # 记录容器中的端口映射
        self._is_sts = False # 是否用 statefulset 来部署

    # 清空app相关的属性
    def clear_app(self):
        self._app = ''  # 应用名
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
            labels = re.sub(r'[^,]+\/', '', df['LABELS']).split(',')
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
        # 执行子步骤
        self.run_steps(steps)
        # 暴露端口
        self.service()
        # 清空app相关的属性
        self.clear_app()

    def config(self, items):
        '''
        生成配置
        :param items 配置项，可以是键值对，可以包含变量，如
              name: shigebeyond
              nginx.conf: ${read_file(./nginx.conf)}
              也可以是变量表达式，如 $cfg 或 ${read_yaml(./cfg.yml)}
        '''
        # 替换变量，支持items是str或dict
        items = replace_var(items, False)
        yaml = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": self.build_metadata("-cfg"),
            "data": items
        }
        self.save_yaml(yaml, '-config.yml')

    def secret(self, items):
        '''
        生成密钥， 其实跟config差不多，只不过config是明文，secret是密文
        :param items 密钥项，可以是键值对，可以包含变量，如
              name: shigebeyond
              nginx.conf: ${read_file(./nginx.conf)}
              也可以是变量表达式，如 $cfg 或 ${read_yaml(./cfg.yml)}
        '''
        # 替换变量，支持items是str或dict
        items = replace_var(items, False)
        yaml = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": self.build_metadata('-secret'),
            "type": "Opaque",
            "data": items
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
            "metadata": self.build_metadata("-rc"),
            "spec": {
                "replicas": option.get("replicas", 1),
                "selector": {
                    "matchLabels": self.build_labels()
                },
                "template": self.build_pod_template()
            }
        }

        self.save_yaml(yaml, '-rc.yml')

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
            "metadata": self.build_metadata("-rs"),
            "spec": {
                "replicas": option.get("replicas", 1),
                "selector": {
                    "matchLabels": self.build_labels()
                },
                "template": self.build_pod_template()
            }
        }

        self.save_yaml(yaml, '-rs.yml')

    def ds(self, option):
        '''
        生成ds
        :params option 部署选项 {node}
                        node 节点过滤，如 "beta.kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'ds')
        yaml = {
            "apiVersion": "v1",
            "kind": "DaemonSet",
            "metadata": self.build_metadata("-ds"),
            "spec": {
                "selector": {
                    "matchLabels": self.build_labels()
                },
                "template": self.build_pod_template(option.get('node'), option.get('tolerations'))
            }
        }

        self.save_yaml(yaml, '-rs.yml')

    def sts(self, option):
        '''
        生成 StatefulSet
        :params option 部署选项 {replicas, node}
                        replicas 副本数
                        node 节点过滤，如 "beta.kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'sts')
        yaml = {
            "apiVersion": "v1",
            "kind": "StatefulSet",
            "metadata": self.build_metadata("-sts"),
            "spec": {
                "replicas": option.get("replicas", 1),
                "serviceName": self._app + "-svc",
                "selector": {
                    "matchLabels": self.build_labels()
                },
                "template": self.build_pod_template()
            }
        }

        self.save_yaml(yaml, '-sts.yml')

        self._is_sts = True

    def deploy(self, option):
        '''
        生成部署
        :params option 部署选项 {replicas, node}
                        replicas 副本数
                        node 节点过滤，如 "beta.kubernetes.io/os": "linux" 或 "disk": "ssd"
        '''
        option = self.fix_replicas_option(option, 'deploy')
        yaml = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": self.build_metadata("-deploy"),
            "spec": {
                "replicas": option.get("replicas", 1),
                "selector": {
                    "matchLabels": self.build_labels()
                },
                "template": self.build_pod_template(option.get('node'), option.get('tolerations'))
            }
        }
        self.save_yaml(yaml, '-deploy.yml')

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

    def build_pod_template(self, node = None, tolerations = None):
        '''
        构建pod模板
        :param node: 只有deploy才有node过滤器
        :param tolerations: 只有deploy才有容忍
        :return:
        '''
        ret = {
            "metadata": {
                "labels": self.build_labels()
            },
            "spec": {
                "containers": self._containers,
                "restartPolicy": "Always",
                "volumes": self._volumes
            }
        }
        # 只有deploy才有node过滤器
        if node:
            if isinstance(node, str):
                node = replace_var(node, False)
            ret["spec"]["nodeSelector"] = node
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
            "metadata": self.build_metadata("-svc"),
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

    def containers(self, containers):
        self._containers = [self.build_container(name, option) for name, option in containers.items()]

    # 构建容器
    def build_container(self, name, option):
        ret = {
            "name": name,
            "image": get_and_del_dict_item(option, 'image'),
            "imagePullPolicy": get_and_del_dict_item(option, 'imagePullPolicy', "IfNotPresent"),
            "env": self.build_env(get_and_del_dict_item(option, 'env')),
            "command": get_and_del_dict_item(option, 'command'),
            "ports": self.build_container_ports(get_and_del_dict_item(option, 'ports')),
            "resources": self.build_resources(get_and_del_dict_item(option, "resources")),
            "volumeMounts": self.build_volume_mounts(get_and_del_dict_item(option, "volumes")),
            "readinessProbe": {
                "exec": {
                    "command": ["/usr/bin/check-status", "-r"]
                }
            }
        }
        ret.update(option)
        del_dict_none_item(ret)
        return ret

    def build_metadata(self, name_postfix):
        meta = {
            "name": self._app + name_postfix,
            "labels": self.build_labels()
        }
        if self._ns:
            meta['namespace'] = self._ns
        return meta

    def build_labels(self):
        return {
            'app': self._app
        }

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
            return {
                'configMap': {
                    'name': self._app + "-cfg",
                    # 'items': [
                    #     {'key': host_path, 'path': host_path}
                    # ]
                }
            }
        # secret: https://www.cnblogs.com/litzhiai/p/11950273.html
        if protocol == 'secret':
            return {
                'secret': {
                    'secretName': host_path,
                }
            }
        raise Exception(f'暂不支持卷协议: {protocol}')

    def build_volume_mounts(self, mounts):
        '''
        构建目录映射
        :params mounts 多行，格式为
                    dir:///apps/fpm729/etc/php-fpm/:/usr/local/etc/php-fpm.d/:rw
                    file:///var/run/docker.sock:/var/run/docker.sock:ro
                    nfs://192.168.159.14/data:/mnt
                    config://mycfg:/etc/mycfg
        '''
        if mounts is None or len(mounts) == 0:
            return None
        if isinstance(mounts, str):
            mounts = [mounts]

        ret = []
        for mount in mounts:
            # 解析末尾的 rw ro
            ro = False # 是否只读
            mat = re.search(':r[wo]', mount)
            if mat is not None:
                ro = mat.group(0) == ':ro'
            '''
            解析协议
            dir:///apps/fpm729/etc/php-fpm/:/usr/local/etc/php-fpm.d/:rw
            file:///var/run/docker.sock:/var/run/docker.sock:ro
            nfs://192.168.159.14/data:/mnt
            config://mycfg:/etc/mycfg
            '''
            protocol = None
            if "://" in mount:
                #  mat = re.search('(\w+)://([\w\d\._]*)(/.+):(.+)', mount)
                mat = re.search('(\w+)://(.+?):(.+)', mount)
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
            elif ':' in mount: # /mycfg:/etc/mycfg
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

            # 记录挂载
            # name = 'vol-' + random_str(10)
            name = 'vol-' + md5(mount_path)
            yaml = {
                "name": name,
                "mountPath": mount_path
            }
            if protocol == 'config':
                yaml['subPath'] = host_path
            if ro:
                yaml['readOnly'] = True
            ret.append(yaml)

            # 记录卷
            vol["name"] = name
            self._volumes.append(vol)
        return ret

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
                  "name": self._app + "-cfg", # The ConfigMap this value comes from.
                  "key": key # The key to fetch.
                }
            }
        }

    # 用在变量赋值中的函数
    def from_secret(self, key):
        return {
            "valueFrom":{
                "secretKeyRef":{
                  "name": self._app + "-secret", # The Secret this value comes from.
                  "key": key # The key to fetch.
                }
            }
        }

    def build_env(self, env):
        if env is None or len(env) == 0:
            return None
        # 整体是变量字符串
        is_whole_var = isinstance(env, str)
        if is_whole_var:
            env = replace_var(env, False)

        ret = []
        for key, val in env.items():
            if not is_whole_var:
                val = replace_var(val)
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
