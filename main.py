# -*- coding: utf-8 -*-
import select
import socket
from typing import Dict,List,Tuple
from urllib.parse import quote, unquote

class AIWrapper():
    # TenHouAI <-> AI_Wrapper <-> Majsoul Interface
    def __init__(self, socket_:socket.socket):
        self.socket = socket_
        self.buffer = bytes(0)

    def recv(self, data:bytes):
        #接受来自AI的tenhou proto数据
        self.buffer += data
        s = self.buffer.split(b'\x00')
        for msg in s[:-1]:
            self._eventHandler(msg.decode('utf-8'))
        self.buffer = s[-1]

    def send(self, data:bytes):
        #向AI发送tenhou proto数据
        print('send:', data)
        self.socket.send(data)

    def _eventHandler(self, msg):
        print('recv:', msg)
        d = self.tenhouDecode(msg)
        funcName = 'on_' + d['opcode']
        if hasattr(self, funcName):
            getattr(self, funcName)(d)
        else:
            print('[AI EVENT] :', msg)

    
    def tenhouDecode(self, msg:str)->Dict:  # get tenhou protocol msg
        l = []
        msg = str.strip(msg)[1:-2] + ' '
        bv = 0
        last_i = 0
        for i in range(len(msg)):
            if msg[i] == '"':
                bv ^= 1
            elif msg[i] == ' ' and not bv:
                l.append(msg[last_i:i])
                last_i = i + 1
        msg = [str.strip(s) for s in l if len(s) > 0]
        d = {s.split('=')[0]: s.split('=')[1][1:-1] for s in msg[1:]}
        d['opcode'] = msg[0]
        return d

    def tenhouEncode(self, kwargs:Dict)->str:  # encode tenhou protocol msg
        opcode = kwargs['opcode']
        s = '<' + str(opcode)
        for k, v in kwargs.items():
            if k != 'opcode':
                s += ' ' + str(k) + '="' + str(v) + '"'
        s += '/>\x00'
        return s
    
    #-------------------------AI回调函数-------------------------

    def on_HELO(self, msg_dict):
        #step 1: init JianYangAI
        self.send(b'<HELO uname="%74%73%74%5F%74%69%6F" auth="20190421-9c033b1f" PF4="9,50,986.91,-4027.0,29,43,71,107,14,1362,162,257,226,135" ratingscale="PF3=1.000000&PF4=1.000000&PF01C=0.582222&PF02C=0.501632&PF03C=0.414869&PF11C=0.823386&PF12C=0.709416&PF13C=0.586714&PF23C=0.378722&PF33C=0.535594&PF1C00=8.000000" rr="PF3=0,0&PF4=272284,0&PF01C=0,0&PF02C=0,0&PF03C=0,0&PF11C=0,0&PF12C=0,0&PF13C=0,0&PF23C=0,0&PF33C=0,0&PF1C00=0,0"/>\x00')

    def on_PXR(self, msg_dict):
        #step 2: init JianYangAI
        self.send(b'<LN n="BgZ1Bdh1Xn1Ik" j="D1C2D2D2D1D12C3B13C1C2B1D12C4D8C1C1B3C2B1C1C1B1B" g="HA3Q1ME1E2BA1Bc4E8Lw3c1Dg12Gc4BQ12BQ4E8M1DM2Bj2Bg2S1t1q1M1BI2S"/>\x00')

    def on_JOIN(self, msg_dict):
        #step 3: init JianYangAI 四人东模式
        self.send(b'<GO type="1" lobby="0" gpid="EE26C0F2-327686F1"/>\x00')
        #step 4: 用户信息
        self.send(('<UN n0="'+quote('tst-tio')+'" n1="'+quote('user1')+'" n2="'+quote('user2')+'" n3="'+quote('user3')+'" dan="9,9,9,0" rate="985.47,1648.57,1379.50,1500.00" sx="M,M,M,M"/>\x00').encode())
        #step 5: fake录像地址
        self.send(('<TAIKYOKU oya="0" log="xxxxxxxxxxxx-xxxx-xxxx-xxxxxxxx"/>\x00').encode())

    def on_NEXTREADY(self, msg_dict):
        # newRound
        seed=[]     # 当前轮数/连庄立直信息
        ten= []     # 当前分数(1ten=100分)
        oya= 0      # 0~3 当前轮我是第几个玩家
        hai= []     # 当前手牌tile136
        assert(len(seed)==6)
        assert(len(ten)==4)
        assert(0<=oya<4)
        assert(len(hai) in (13,14))
        self.send(('<INIT seed="'+','.join(str(i) for i in seed)+'" ten="'+','.join(str(i) for i in ten)+'" oya="'+str(oya)+'" hai="'+','.join(str(i) for i in hai)+'"/>\x00').encode())


def MainLoop():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_address = ('localhost', 7479)
    print('starting up on %s port %s' % server_address)
    server.bind(server_address)

    server.listen(1)
    print('\nwaiting for the AI')
    connection, client_address = server.accept()
    print('AI connection: ',type(connection),connection,client_address)
    aiWrapper = AIWrapper(connection)

    inputs=[connection]
    outputs=[]

    while True:
        readable, writable, exceptional = select.select(inputs, outputs, inputs, 0.5)
        for s in readable:
            data = s.recv(1024)
            if data:
                # A readable client socket has data
                aiWrapper.recv(data)
            else:
                # Interpret empty result as closed connection
                print('closing', client_address, 'after reading no data')
                return
        # Handle "exceptional conditions"
        for s in exceptional:
            print('handling exceptional condition for', s.getpeername())
            return


if __name__=='__main__':
    MainLoop()