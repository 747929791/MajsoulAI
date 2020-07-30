# MajsoulAI
**MajsoulAI**项目旨在实现在《雀魂》中用AI代替玩家打麻将。MajsoulAI前端使用[majsoul_wrapper](https://github.com/747929791/majsoul_wrapper)通过监听《雀魂》websocket对局信息作为输入，以基于图像识别的模拟鼠标点击作为输出。MajsoulAI后端桥接至一个开源的天凤麻将AI([JianYangAI](https://github.com/erreurt/MahjongAI))。

## 使用说明

### 准备工作

我们**强烈建议**在安装与运行之前阅读[majsoul_wrapper](https://github.com/747929791/majsoul_wrapper)的使用说明，这是MajsoulAI的基础。

在安装完成majsoul_wrapper之后，一切都将变得水到渠成，因为这个项目并没有什么其他需要安装的了。

运行之前首先需要启动mitmproxy：

```bash
$python -m majsoul_wrapper
```
或在majsoul_wrapper目录里执行：

```bash
./majsoul_wrapper$ mitmdump -s addons.py
```

此时你应该可以看到一个新启动的Chrome，在这个Chrome窗口里打开[雀魂](http://www.maj-soul.com/)并登陆至主界面（目前雀魂国际化版本只支持**繁体中文**，简体的button定位未实现）。由于MajsoulAI是基于图像识别的，所以接下来AI启动与运行时需始终保持mitmproxy存活，且校准过程中保持雀魂主界面（段位場、比賽場、友人場）始终完整的置于屏幕顶层（位置与大小不重要，不是太小即可）。


### 启动AI

MajsoulAI支持两种AI调用模式。一种在本地启动AI，并通过IPC进行数据通信；另一种可以将AI托管在一个有公网IP的远程服务器，并通过网络通信调用AI（远程AI模式会更快，因为本地还需渲染雀魂页面，计算开销较大）。

本地AI模式可直接输入：
```bash
$python main.py
```

远程AI模式下，需先在远程服务器上clone MajsoulAI，并打开AI服务进程：
```bash
$python remote.py
```
该进程将持续监听**14782**端口等待客户端连接，然后在本机以remote模式运行客户端进程：
```bash
$python main.py --remote_ip REMOTE_ADDRESS
```

成功之后你将看到AI接入的信息。

Majsoul同时支持自动匹配模式与手动匹配模式两种方式，以-l或--level参数区分。手动匹配是默认模式，需在AI启动并校准界面以后，手动选择要进行的对局，当对局开始后AI将自动唤醒并接管鼠标操作；自动模式下AI将从主菜单开始，无限的循环进行段位场匹配。

手动AI模式：
```bash
$python main.py
```

自动AI模式：
```bash
$python main.py --level L
```

数字L可选值为{0,1,2,3,4}，分别对应{铜/银/金/玉/王座之间}

上述两种模式可以自由搭配，例如在远程服务器173.252.110.21上启动**远程AI**，并持续进行**银之间**段位场匹配，那么应在远程服务器启动remote.py后在本地执行：
```bash
$python main.py --remote_ip 173.252.110.21 --level 1
```

至此你就可以看到自主出牌的雀魂AI了！