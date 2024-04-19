# command-r

### 运行容器
运行容器，可以通过环境变量传递配置信息，例如代理URL和监听的端口。以下是一个示例：

```
version: '3'
services:
  command-r:
    container_name: command-r
    image: coulsontl/command-r
    network_mode: bridge
    restart: always
    ports:
      - '3030:3030'
    environment:
      - TZ=Asia/Shanghai
      - HTTP_PROXY=http://user:password@yourproxy:port
      - SERVER_PORT=3030
```

### 环境变量说明
* HTTP_PROXY: 指定所有请求通过的代理服务器的URL
* SERVER_PORT: 代表监听的端口，默认3030
* 如果有多个反代站点直接添加多个以SERVER_PORT_开头的环境变量就行了
