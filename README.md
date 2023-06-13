[GitHub](https://github.com/shigebeyond/K8sBoot) | [Gitee](https://gitee.com/shigebeyond/K8sBoot)

# K8sBoot - yaml驱动k8s命令与配置生成

## 概述
k8s太复杂了，特别是命令与配置，学习与使用成本很高，大部分伙伴很难学会，因此创作了K8sBoot工具，支持通过简化版的yaml配置来生成k8s最终的命令与配置；

框架通过编写简单的yaml, 就可以执行一系列复杂的操作步骤, 如打印变量等，极大的简化了伙伴编写k8s配置的工作量与工作难度，大幅提高人效；

框架通过提供类似python`for`/`if`/`break`语义的步骤动作，赋予伙伴极大的开发能力与灵活性，能适用于广泛的应用场景。

框架提供`include`机制，用来加载并执行其他的步骤yaml，一方面是功能解耦，方便分工，一方面是功能复用，提高效率与质量，从而推进脚本整体的工程化。

## 特性
1. 支持通过yaml来配置执行的步骤，简化了生成代码的开发:
每个步骤可以有多个动作，但单个步骤中动作名不能相同（yaml语法要求）;
动作代表excel上的一种操作，如switch_sheet/export_df等等;
2. 支持类似python`for`/`if`/`break`语义的步骤动作，灵活适应各种场景
3. 支持`include`引用其他的yaml配置文件，以便解耦与复用

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

如执行 `K8sBoot example/step-dbschema.yml`，输出如下
```
shi@shi-PC:[/home/shi/code/python/K8sBoot]: K8sBoot example/step-dbschema.yml 
2022-12-06 19:08:17,916 - boot - DEBUG - Load and run step file: /ohome/shi/code/python/K8sBoot/example/step-dbschema.yml
2022-12-06 19:08:17,921 - boot - DEBUG - handle action: connect_db={'ip': '192.168.62.200', 'port': 3306, 'dbname': 'test', 'user': 'root', 'password': 'root', 'echo_sql': True}
2022-12-06 19:08:17,937 - boot - DEBUG - handle action: start_edit=data/test数据结构.xlsx
2022-12-06 19:08:17,938 - boot - DEBUG - handle action: switch_sheet=目录
2022-12-06 19:08:17,938 - boot - DEBUG - handle action: query_db={'tables': "SELECT\n    TABLE_COMMENT as 表注释,\n    TABLE_NAME as 表名\nFROM\n    information_schema. TABLES\nWHERE\n    TABLE_SCHEMA = 'test'\n"}
2022-12-06 19:08:17,938 - boot - DEBUG - SELECT
    TABLE_COMMENT as 表注释,
    TABLE_NAME as 表名
FROM
    information_schema. TABLES
WHERE
    TABLE_SCHEMA = 'test'
......
```
命令会自动操作并生成excel

## 步骤配置文件及demo
用于指定多个步骤, 示例见源码 [example](example) 目录下的文件;

顶级的元素是步骤;

每个步骤里有多个动作(如switch_sheet/export_df)，如果动作有重名，就另外新开一个步骤写动作，这是由yaml语法限制导致的，但不影响步骤执行。

简单贴出2个demo
1. 导出数据库中的表与字段: 详见 [example/step-dbschema.yml](example/step-dbschema.yml)

2. 根据sql来生成各种plot绘图, 支持1 line 折线图 2 bar 条形图 3 barh 横向条形图 4 hist 直方图 5 box 箱线图 6 kde 核密度图 7 pie 饼图;
详见 [example/step-plot.yml](example/step-plot.yml)
![plot绘图](img/plot.png)

## 配置详解
支持通过yaml来配置执行的步骤;

每个步骤可以有多个动作，但单个步骤中动作名不能相同（yaml语法要求）;

动作代表excel上的一种操作，如switch_sheet/export_df等等;

下面详细介绍每个动作:

1. print: 打印, 支持输出变量/函数; 
```yaml
# 调试打印
print: "总申请数=${dyn_data.total_apply}, 剩余份数=${dyn_data.quantity_remain}"
```



25. for: 循环; 
for动作下包含一系列子步骤，表示循环执行这系列子步骤；变量`for_i`记录是第几次迭代（从1开始）,变量`for_v`记录是每次迭代的元素值（仅当是list类型的变量迭代时有效）
```yaml
# 循环3次
for(3) :
  # 每次迭代要执行的子步骤
  - switch_sheet: test

# 循环list类型的变量urls
for(urls) :
  # 每次迭代要执行的子步骤
  - switch_sheet: test

# 无限循环，直到遇到跳出动作
# 有变量for_i记录是第几次迭代（从1开始）
for:
  # 每次迭代要执行的子步骤
  - break_if: for_i>2 # 满足条件则跳出循环
    switch_sheet: test
```

26. once: 只执行一次，等价于 `for(1)`; 
once 结合 moveon_if，可以模拟 python 的 `if` 语法效果
```yaml
once:
  # 每次迭代要执行的子步骤
  - moveon_if: for_i<=2 # 满足条件则往下走，否则跳出循环
    switch_sheet: test
```

27. break_if: 满足条件则跳出循环; 
只能定义在for/once循环的子步骤中
```yaml
break_if: for_i>2 # 条件表达式，python语法
```

28. moveon_if: 满足条件则往下走，否则跳出循环; 
只能定义在for/once循环的子步骤中
```yaml
moveon_if: for_i<=2 # 条件表达式，python语法
```

29. if/else: 满足条件则执行if分支，否则执行else分支
```yaml
- set_vars:
    txt: '进入首页'
- if(txt=='进入首页'): # 括号中包含的是布尔表达式，如果表达式结果为true，则执行if动作下的子步骤，否则执行else动作下的子步骤
    - print: '----- 执行if -----'
  else:
    - print: '----- 执行else -----'
```

30. include: 包含其他步骤文件，如记录公共的步骤，或记录配置数据(如用户名密码); 
```yaml
include: part-common.yml
```

31. set_vars: 设置变量; 
```yaml
set_vars:
  name: shi
  password: 123456
  birthday: 5-27
```

32. print_vars: 打印所有变量; 
```yaml
print_vars:
```