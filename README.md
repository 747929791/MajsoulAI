# MajsoulAI
这个项目的目标是使用majsoul_wrapper即使抓取《雀魂》对局信息，并桥接至一个开源的天凤麻将AI，实现在雀魂中用AI打麻将。

## Usage

首先打开浏览器并连接至mitmproxy，并打开并登陆进雀魂主菜单
```
./majsoul_wrapper$ mitmdump -s addons.py
```
由于output是基于图像识别的，所以需始终保持雀魂界面置于屏幕顶层

运行AI：
```
./$ python main.py
```
打开一个对局，AI将自动接入并开始自主出牌！