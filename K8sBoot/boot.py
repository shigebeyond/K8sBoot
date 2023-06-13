#!/usr/bin/python3
# -*- coding: utf-8 -*-

import fnmatch
import json
import os
from pyutilb.util import *
from pyutilb.file import *
from pyutilb.cmd import *
from pyutilb import YamlBoot, BreakException
from pyutilb.log import log
import platform


# k8s配置生成的基于yaml的启动器
class Boot(YamlBoot):

    def __init__(self):
        super().__init__()
        # 动作映射函数
        actions = {
            'start_edit': self.start_edit,
        }
        self.add_actions(actions)

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

    # 设置命名空间
    def namespace(self, name):
        self.ns = name

    # 设置app名
    def app(self, name):
        self.appname = name

    # 设置配置
    def config(self, options):
        pass

    # 设置卷
    def volumn(self, options):
        pass

    # 设置密钥
    def secret(self, options):
        pass

    # 设置服务: 内部实现pod+deploy+service
    def service(self, options):
        pass

    def build_metadata(self, name_postfix):
        meta = {
            "name": self.appname + name_postfix,
            "labels": self.build_labels()
        }
        if self.ns:
            meta['namespace'] = self.ns
        return meta

    def build_labels(self):
        return {
            'app': self.appname
        }

    def gen_cfg(self):
        # 配置文件转dict
        files = []
        data = {}
        for file in files:
            data[file] = read_file(file)

        yaml = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": self.build_metadata("-cfg"),
            "data": data
        }

    def gen_ns(self):
        yaml = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": self.ns,
        }

    def gen_rc(self):
        yaml = {
            "apiVersion": "vl",
            "kind": "ReplicationController",
            "metadata": self.build_metadata("-rc"),
            "spec": {
                "replicas": 2,
                "template": {
                    "spec": {
                        "containers": [{
                            "name": "myweb",
                            "image": "kubeguide/tomcat-app:vl",
                            "ports": {
                                "containerPort": 8080
                            }
                        }]
                    }
                }
            }
        }

        file = self.appname + '-rc.yml'

    def gen_deploy(self):
        yaml = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": self.build_metadata("-deploy"),
            "spec": {
                "replicas": 1,
                "selector": {
                    "matchLabels": self.build_labels()
                },
                "template": {
                    "metadata": {
                        "labels": self.build_labels()
                    },
                    "spec": {
                        "nodeSelector": {
                            "beta.kubernetes.io/os": "linux"
                        },
                        "tolerations": [{
                            "key": "CriticalAddonsOnly",
                            "operator": "Exists"
                        }, {
                            "key": "node-role.kubernetes.io/master",
                            "effect": "NoSchedule"
                        }],
                        "containers": [
                            self.build_container('test', None)
                        ]
                    }
                }
            }
        }

    def build_container(self, name, option):
        return {
            "name": name,
            "image": option['image'],
            "imagePullPolicy": "IfNotPresent",
            "env": self.build_env(),
            "command": option['command'],
            "ports": [{
                "containerPort": 80
            }],
            "readinessProbe": {
                "exec": {
                    "command": ["/usr/bin/check-status", "-r"]
                }
            }
        }

    # 用在变量赋值中的函数
    def field_val(self, field):
        return {
            "valueFrom":{
                "fieldRef": {
                    "fieldPath": field
                }
            }
        }

    # 用在变量赋值中的函数
    def config_val(self, file, key):
        return {
            "valueFrom":{
                "configMapKeyRef":{
                  "name": file, # The ConfigMap this value comes from.
                  "key": key # The key to fetch.
                }
            }
        }

    def build_env(self):
        ret = []
        for key, val in self.env:
            val = replace_var(val)
            ret.append({
                "name": key,
                "value": val
            })
        return ret

    def gen_svc(self):
        ports = [{
            'port': 8078,
            'name': 'http',
            'targetPort': 80,
            # "nodePort": 30001, # 仅对 type=NodePort
            'protocol': 'TCP'
        }]
        yaml = {
            "apiVersion": "vl",
            "kind": "Service",
            "metadata": self.build_metadata("-svc"),
            "spec": {
                # "type": "NodePort",
                "ports": ports,
                "selector": self.build_labels()
            }
        }
        file = self.appname + '-svc.yml'


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
    # main()
    data = read_yaml('/home/shi/code/k8s/test.yaml')
    print(json.dumps(data))
