# 复制jars
if [ -d "/input/jars" ]; then
    cp /input/jars/* /opt/bitnami/spark/jars/
fi
# 启动spark, 本来就是镜像的启动命令
/opt/bitnami/scripts/spark/entrypoint.sh /opt/bitnami/scripts/spark/run.sh