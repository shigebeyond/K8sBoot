[GitHub](https://github.com/shigebeyond/K8sBoot) | [Gitee](https://gitee.com/shigebeyond/K8sBoot)

# K8sBoot - yaml驱动k8s配置生成

## 概述
k8s太复杂了，特别是配置，学习与使用成本很高，大部分伙伴很难学会，因此创作了K8sBoot工具，支持通过简化版的yaml配置来生成k8s最终的配置文件；

框架通过编写简单的yaml, 就可以执行一系列复杂的操作步骤, 如打印变量/生成rc/rs/deploy等资源文件，极大的简化了伙伴编写k8s配置文件的工作量与工作难度，大幅提高人效；

框架通过提供类似python`for`/`if`/`break`语义的步骤动作，赋予伙伴极大的开发能力与灵活性，能适用于广泛的应用场景。

框架提供`include`机制，用来加载并执行其他的步骤yaml，一方面是功能解耦，方便分工，一方面是功能复用，提高效率与质量，从而推进脚本整体的工程化。

## 特性
1. 支持通过yaml来配置执行的步骤，简化了生成代码的开发:
每个步骤可以有多个动作，但单个步骤中动作名不能相同（yaml语法要求）;
动作代表k8s的某个资源定义，如config/rc/rs/deploy等等;
2. 支持类似python`for`/`if`/`break`语义的步骤动作，灵活适应各种场景
3. 支持`include`引用其他的yaml配置文件，以便解耦与复用

## 搭配k8s命令简化框架，使用更简单
[k8scmd](https://github.com/shigebeyond/k8scmd)：对k8s的复杂命令做了大量简化

## 同类yaml驱动框架
[HttpBoot](https://github.com/shigebeyond/HttpBoot)
[SeleniumBoot](https://github.com/shigebeyond/SeleniumBoot)
[AppiumBoot](https://github.com/shigebeyond/AppiumBoot)
[MiniumBoot](https://github.com/shigebeyond/MiniumBoot)
[ExcelBoot](https://github.com/shigebeyond/ExcelBoot)
[MonitorBoot](https://github.com/shigebeyond/MonitorBoot)

## todo
1. 支持更多的动作

## 安装
```
pip3 install K8sBoot
```

安装后会生成命令`K8sBoot`;

注： 对于深度deepin-linux系统，生成的命令放在目录`~/.local/bin`，建议将该目录添加到环境变量`PATH`中，如
```
export PATH="$PATH:/home/shi/.local/bin"
```

## 使用
```
# 1 执行单个文件
K8sBoot 步骤配置文件.yml

# 2 执行多个文件
K8sBoot 步骤配置文件1.yml 步骤配置文件2.yml ...

# 3 执行单个目录, 即执行该目录下所有的yml文件
K8sBoot 步骤配置目录

# 4 执行单个目录下的指定模式的文件
K8sBoot 步骤配置目录/step-*.yml
```

如执行 `K8sBoot example/ingress/1hello.yml -o data/`，输出如下
```
shi@shi-PC:[~/code/python/K8sBoot]: K8sBoot example/ingress/1hello.yml -o data/
2023-07-10 18:29:02,857 - ThreadPoolExecutor-0_0        - boot - DEBUG - Load and run step file: /home/shi/code/python/K8sBoot/example/ingress/1hello.yml
2023-07-10 18:29:02,860 - ThreadPoolExecutor-0_0        - boot - DEBUG - handle action: app(hello)=[{'containers': {'hello': {'image': 'registry.cn-hangzhou.aliyuncs.com/lfy_k8s_images/hello-server', 'ports': ['8000:9000']}}}, {'deploy': {'replicas': 2}}]
2023-07-10 18:29:02,860 - ThreadPoolExecutor-0_0        - boot - DEBUG - handle action: containers={'hello': {'image': 'registry.cn-hangzhou.aliyuncs.com/lfy_k8s_images/hello-server', 'ports': ['8000:9000']}}
2023-07-10 18:29:02,860 - ThreadPoolExecutor-0_0        - boot - DEBUG - handle action: deploy={'replicas': 2}
2023-07-10 18:29:02,862 - ThreadPoolExecutor-0_0        - boot - INFO - App[hello]的资源配置文件已生成完毕, 如要更新到集群中的资源请手动执行: kubectl apply --record=true -f /home/shi/code/python/K8sBoot/data
```
命令会自动操作并生成k8s资源文件
```
shi@shi-PC:[~/code/python/K8sBoot]: tree data
data
├── hello-deploy.yml
└── hello-svc.yml
```

## 步骤配置文件及demo
用于指定多个步骤, 示例见源码 [example](example) 目录下的文件;

顶级的元素是步骤;

每个步骤里有多个动作(如config/rc/rs/deploy)，如果动作有重名，就另外新开一个步骤写动作，这是由yaml语法限制导致的，但不影响步骤执行。

## 配置详解
支持通过yaml来配置执行的步骤;

每个步骤可以有多个动作，但单个步骤中动作名不能相同（yaml语法要求）;

动作代表k8s上的一种操作，如config/rc/rs/deploy等等;

下面详细介绍每个动作

### 基本动作
1. ns：设置与生成 namespace 资源
```yaml
ns: 命名空间名
```

2. app：生成应用，并执行子步骤
```yaml
app(应用名):
	# 子步骤
    - config:
        auther: shigebeyond
```

3. print: 打印, 支持输出变量/函数; 
```yaml
# 调试打印
print: "总申请数=${dyn_data.total_apply}, 剩余份数=${dyn_data.quantity_remain}"
```

4. set_vars: 设置变量; 
```yaml
set_vars:
  name: shi
  password: 123456
  birthday: 5-27
```

5. print_vars: 打印所有变量; 
```yaml
print_vars:
```

6. for: 循环; 
for动作下包含一系列子步骤，表示循环执行这系列子步骤；变量`for_i`记录是第几次迭代（从1开始）,变量`for_v`记录是每次迭代的元素值（仅当是list类型的变量迭代时有效）
```yaml
# 循环3次
for(3) :
  # 每次迭代要执行的子步骤
  - print: $for_v

# 循环list类型的变量urls
for(urls) :
  # 每次迭代要执行的子步骤
  - print: $for_v

# 无限循环，直到遇到跳出动作
# 有变量for_i记录是第几次迭代（从1开始）
for:
  # 每次迭代要执行的子步骤
  - break_if: for_i>2 # 满足条件则跳出循环
    print: $for_v
```

7. once: 只执行一次，等价于 `for(1)`; 
once 结合 moveon_if，可以模拟 python 的 `if` 语法效果
```yaml
once:
  # 每次迭代要执行的子步骤
  - moveon_if: for_i<=2 # 满足条件则往下走，否则跳出循环
    print: $for_v
```

8. break_if: 满足条件则跳出循环; 
只能定义在for/once循环的子步骤中
```yaml
break_if: for_i>2 # 条件表达式，python语法
```

9. moveon_if: 满足条件则往下走，否则跳出循环; 
只能定义在for/once循环的子步骤中
```yaml
moveon_if: for_i<=2 # 条件表达式，python语法
```

10. if/else: 满足条件则执行if分支，否则执行else分支
```yaml
- set_vars:
    txt: '进入首页'
- if(txt=='进入首页'): # 括号中包含的是布尔表达式，如果表达式结果为true，则执行if动作下的子步骤，否则执行else动作下的子步骤
    - print: '----- 执行if -----'
  else:
    - print: '----- 执行else -----'
```

11. include: 包含其他步骤文件，如记录公共的步骤，或记录配置数据(如用户名密码); 
```yaml
include: part-common.yml
```

### app作用域下的子动作
以下的动作，必须声明在app动作的子步骤中，动作的参数支持传递变量

12. labels：设置应用标签
```yaml
labels: 
	env: prod
```

13. config：以键值对的方式来设置 Config 资源
```yaml
config:
    auther: shigebeyond
```

14. config_from_files：以文件内容的方式来设置 Config 资源，在挂载configmap时items默认填充用config_from_files()写入的key
```yaml
config_from_files: # 配置文件
    - ./default.conf
    - ./index.php
```

15. secret：以键值对的方式来设置 Secret 资源
```yaml
secret:
    auther: c2hpZ2ViZXlvbmQK
```

16. secret_files：以文件内容的方式来设置 Secret 资源，在挂载secret时items默认填充用secret_files()写入的key
```yaml
config_from_files: # secret文件
    - ./admin.conf
```

17. containers：设置容器，用于生成资源 pod / ReplicationController / ReplicaSet / DaemonSet / StatefulSet / Deployment / Job / Cronjob / HorizontalPodAutoscaler 文件中的 `spec.containers` 元素
```yaml
containers:
    nginx: # 定义多个容器, dict形式, 键是容器名, 值是容器配置
      image: nginx # 镜像
      env: # 以dict方式设置环境变量
        TZ: Asia/Shanghai
        # 引用pod信息
        POD_NAME: ${ref_pod_field(metadata.name)}
        POD_NAMESPACE: ${ref_pod_field(metadata.namespace)}
        POD_IP: ${ref_pod_field(status.podIP)}
        # 引用容器资源信息
        CPU_MIN: ${ref_resource_field(requests.cpu)}
        CPU_MAX: ${ref_resource_field(limits.cpu)}
        MEM_MIN: ${ref_resource_field(requests.memory)}
        MEM_MAX: ${ref_resource_field(limits.memory)}
        # 引用配置
        AUTHOR: ${ref_config(auther)}
      env_from: # 从当前应用的 config 或 secret 资源中导入环境变量
        - config
        #- secret
      ports: # 端口映射
        - 80 # 容器端口
        #- 30000:80 # 服务端口:容器端口
        #- 30000:30000:80 # 宿主机端口:服务端口:容器端口
        #- udp://30000:80 # 前面加协议，默认tcp
      volumes: # 卷映射
        - /var/log/nginx
        #- /lnmp/www:/www
        # nginx配置文件挂载： https://blog.csdn.net/weixin_47415962/article/details/116003059
        - config://:/www # 挂载configmap所有key到目录
        - config://default.conf:/etc/nginx/conf.d/default.conf # 挂载configmap单个key到文件
        - downwardAPI://:/etc/podinfo # 将元数据labels和annotations以文件的形式挂载到目录
        - downwardAPI://labels:/etc/podinfo2/labels.properties # 将元数据labels挂载为文件
      # 启动命令：命令改写后导致nginx自身服务没起来，应该是覆盖了nginx镜像自身的启动命令
      #command: sed -i 's/POD_IP/\$POD_IP/g' /www/index.html; tail -f /etc/profile
      #command: while true;do echo hello;sleep 1;done # 死循环维持pod运行
      ready?: # 就绪态
        # 各种秒数
        #seconds: initialDelaySeconds=5 periodSeconds=5 timeoutSeconds=5 successThreshold=1 failureThreshold=5 # 全写
        seconds: i=5 p=5 t=5 s=1 f=5 # 简写
        # 动作
        action: ls /etc/nginx/
      live?: # 存活性探针
        # 各种秒数
        #seconds: initialDelaySeconds=5 periodSeconds=5 timeoutSeconds=5 successThreshold=1 failureThreshold=5 # 全写
        seconds: i=5 p=5 t=5 s=1 f=5 # 简写
        # 动作
        action: http://localhost:80 # 在pod中执行，请使用容器端口
      resources: # 资源
        cpu: 0.01
        memory: 50Mi
```

18. initContainers：设置初始化容器，用于生成资源 pod / ReplicationController / ReplicaSet / DaemonSet / StatefulSet / Deployment / Job / Cronjob / HorizontalPodAutoscaler 文件中的 `spec.initContainers` 元素
```yaml
initContainers: 
	# 参数跟 containers 动作一样
```

19. pod：生成 pod 资源
```yaml
pod:
```

20. deploy：生成 Deployment 资源
```yaml
deploy:
	replicas: 1 # 副本数
# 简写
deploy: 1

# 更详细的参数
deploy:
    replicas: 2 # 副本数
    nodeSelector: # 节点选择: dict形式
      kubernetes.io/os: linux
    nodeAffinity:
      require: # requiredDuringSchedulingIgnoredDuringExecution简写
        - kubernetes.io/os in linux # 标签选择的表达式
      prefer: # preferredDuringSchedulingIgnoredDuringExecution简写
        - kubernetes.io/os in linux
      weight: 1
    podAffinity:
      require: # requiredDuringSchedulingIgnoredDuringExecution简写
        - app = nginx
      prefer: # preferredDuringSchedulingIgnoredDuringExecution简写
        - app = nginx
      weight: 1
      tkey: kubernetes.io/hostname # topologyKey简写
    podAntiAffinity:
      require: # requiredDuringSchedulingIgnoredDuringExecution简写
        - app = xxx
      prefer: # preferredDuringSchedulingIgnoredDuringExecution简写
        - app = xxx
      weight: 1
      tkey: kubernetes.io/hostname # topologyKey简写
    tolerations: # 容忍
      - node-role.kubernetes.io/master:NoSchedule
      - node-role.kubernetes.io/control-plane:NoSchedule
```

21. rc：生成 ReplicationController 资源
```yaml
rc:
	replicas: 1 # 副本数
# 简写
rc: 1
# 更详细的参数：参考 deploy 动作
```

22. rs：生成 ReplicaSet 资源
```yaml
rs:
	replicas: 1 # 副本数
# 简写
rs: 1
# 更详细的参数：参考 deploy 动作
```

23. ds：生成 DaemonSet 资源
```yaml
ds:
	replicas: 1 # 副本数
# 简写
ds: 1
# 更详细的参数：参考 deploy 动作
```

24. sts：生成 StatefulSet 资源
```yaml
sts:
	replicas: 1 # 副本数
# 简写
sts: 1
# 更详细的参数：参考 deploy 动作
```

25. job：生成 Job 资源
```yaml
job:
	replicas: 1 # 副本数
# 简写
job: 1
# 更详细的参数：参考 deploy 动作
```

26. cronjob：生成 Cronjob 资源
```yaml
cronjob:
	replicas: 1 # 副本数
# 简写
cronjob: 1
# 更详细的参数：参考 deploy 动作
```

27. hpa：生成 HorizontalPodAutoscaler 资源
```yaml
hpa:
	replicas: 1 # 副本数
# 简写
hpa: 1
# 更详细的参数：参考 deploy 动作
```

28. ingress：生成 Ingress 资源
```yaml
ingress:
    # url对转发的(服务)端口映射，支持字典树形式
    http://k8s.com/a: 80 # 当前应用的服务端口
    http://k8s.com/b: nginx:80 # 指定应用的服务端口
    # 等价于
    http://k8s.com:
      /c: 80 # 当前应用的服务端口
      /d: nginx:80 # 指定应用的服务端口
    # 路径重写，如果是/api/hello，则去掉前缀api，访问服务的/hello，网关一般这么搞
    http://k8s.com/api(/|$)(.*): 80  
```