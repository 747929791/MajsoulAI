# -*- coding: utf-8 -*-
import time
import select
import socket
import pickle
from typing import Dict, List, Tuple
from urllib.parse import quote, unquote
from enum import Enum
from xmlrpc.client import ServerProxy
from subprocess import Popen, CREATE_NEW_CONSOLE

import majsoul_wrapper as sdk
from majsoul_wrapper import all_tiles, Operation


class State(Enum):  # 控制AI进程与Majsoul进程同步
    WaitingForStart = 0
    Playing = 1


class CardRecorder:
    # 由于雀魂不区分相同牌的编号，但天凤区分tile136，需要根据出现的顺序转换
    def __init__(self):
        self.clear()

    def clear(self):
        self.cardDict = {tile: 0 for tile in sdk.all_tiles}

    def majsoul2tenhou(self, tile: str) -> Tuple[int, int]:
        # tileStr to (tile136,tile34) (e.g. '0s' -> (88,37)
        t = 'mpsz'.index(tile[-1])
        if tile[0] == '0':
            #红宝牌
            return [(16, 35), (52, 36), (88, 37)][t]
        else:
            tile136 = (ord(tile[0])-ord('0')-1)*4+9*4*t
            if tile[0] == '5' and t < 3:  # 5 m|p|s
                tile136 += 1
            tile136 += self.cardDict[tile]
            self.cardDict[tile] += 1
            tile34 = tile136//4
            return (tile136, tile34)

    def tenhou2majsoul(self, tile136=None, tile34=None):
        # (tile136,tile34) to tileStr
        if tile136 != None:
            assert(tile34 == None)
            tile136 = int(tile136)
            if tile136 in (16, 52, 88):
                #红宝牌
                return '0'+'mps'[(16, 52, 88).index(tile136)]
            else:
                return str((tile136//4) % 9+1)+'mpsz'[tile136//36]
        else:
            assert(tile136 == None)
            tile34 = int(tile34)
            if tile34 > 34:
                #红宝牌
                return '0'+'mps'[tile34-35]
            else:
                return str(tile34 % 9+1)+'mpsz'[tile34//9]


class AIWrapper(sdk.GUIInterface, sdk.MajsoulHandler):
    # TenHouAI <-> AI_Wrapper <-> Majsoul Interface

    def __init__(self):
        super().__init__()
        self.AI_socket = None
        # 与Majsoul的通信
        self.majsoul_server = ServerProxy(
            "http://127.0.0.1:37247")  # 初始化RPC服务器
        self.liqiProto = sdk.LiqiProto()
        # 牌号转换
        self.cardRecorder = CardRecorder()

    def init(self, socket_: socket.socket):
        # 设置与AI的socket链接并初始化
        self.AI_socket = socket_
        self.AI_buffer = bytes(0)
        self.AI_state = State.WaitingForStart
        #  与Majsoul的通信
        self.majsoul_history_msg = []  # websocket flow_msg
        self.majsoul_msg_p = 0  # 当前准备解析的消息下标
        self.liqiProto.init()
        # AI上一次input操作的msg_dict(维护tile136一致性)
        self.lastOp = self.tenhouEncode({'opcode': None})
        self.lastDiscard = None  # 牌桌最后一次出牌tile136，用于吃碰杠牌号
        self.hai = []           # 我当前手牌的tile136编号(和AI一致)
        self.isLiqi = False     # 当前是否处于立直状态

    def isPlaying(self) -> bool:
        # 从majsoul websocket中获取数据，并判断数据流是否为对局中
        n = self.majsoul_server.get_len()
        liqiProto = sdk.LiqiProto()
        if n == 0:
            return False
        flow = pickle.loads(self.majsoul_server.get_items(0, min(100, n)).data)
        for flow_msg in flow:
            result = liqiProto.parse(flow_msg)
            if result.get('method', '') == '.lq.FastTest.authGame':
                return True
        return False

    def recvFromMajsoul(self):
        # 从majsoul websocket中获取数据，并尝试解析执行。
        # 如果未达到要求无法执行则锁定self.majsoul_msg_p直到下一次尝试。
        n = self.majsoul_server.get_len()
        l = len(self.majsoul_history_msg)
        if l < n:
            flow = pickle.loads(self.majsoul_server.get_items(l, n).data)
            self.majsoul_history_msg = self.majsoul_history_msg+flow
            pickle.dump(self.majsoul_history_msg, open(
                'websocket_frames.pkl', 'wb'))
        if self.majsoul_msg_p < n:
            flow_msg = self.majsoul_history_msg[self.majsoul_msg_p]
            result = self.liqiProto.parse(flow_msg)
            failed = self.parse(result)
            if not failed:
                self.majsoul_msg_p += 1

    def recv(self, data: bytes):
        #接受来自AI的tenhou proto数据
        self.AI_buffer += data
        s = self.AI_buffer.split(b'\x00')
        for msg in s[:-1]:
            self._eventHandler(msg.decode('utf-8'))
        self.AI_buffer = s[-1]

    def send(self, data: bytes):
        #向AI发送tenhou proto数据
        if type(data) == str:
            data = data.encode()
        print('send:', data)
        self.AI_socket.send(data)

    def _eventHandler(self, msg):
        #解析AI发来的数据
        print('recv:', msg)
        d = self.tenhouDecode(msg)
        if self.AI_state == State.WaitingForStart:
            funcName = 'on_' + d['opcode']
            if hasattr(self, funcName):
                getattr(self, funcName)(d)
            else:
                print('[AI EVENT] :', msg)
        elif self.AI_state == State.Playing:
            op = d['opcode']
            if op == 'D':
                #出牌
                self.on_DiscardTile(d)
            elif op == 'N':
                #回应吃碰杠
                self.on_ChiPengGang(d)
            elif op == 'REACH':
                #宣告自己立直
                self.on_Liqi(d)

    def tenhouDecode(self, msg: str) -> Dict:  # get tenhou protocol msg
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

    def tenhouEncode(self, kwargs: Dict) -> str:  # encode tenhou protocol msg
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
        self.send(('<UN n0="'+quote('tst-tio')+'" n1="'+quote('user1')+'" n2="'+quote('user2')+'" n3="' +
                   quote('user3')+'" dan="9,9,9,0" rate="985.47,1648.57,1379.50,1500.00" sx="M,M,M,M"/>\x00').encode())
        #step 5: fake录像地址
        self.send(
            ('<TAIKYOKU oya="0" log="xxxxxxxxxxxx-xxxx-xxxx-xxxxxxxx"/>\x00').encode())

    def on_NEXTREADY(self, msg_dict):
        # newRound
        self.AI_state = State.Playing
    #-------------------------Majsoul回调函数-------------------------

    def newRound(self, ju: int, ben: int, tiles: List[str], scores: List[int], leftTileCount: int, doras: List[str]):
        """
        ju:当前第几局(0:东1局,3:东4局，连庄不变，TODO:南)
        ben:连装棒数量(画面左上角八个黑点的棒)
        TODO:流局立直棒数量(画面左上角一个红点的棒)
        tiles:我的初始手牌
        scores:当前场上四个玩家的剩余分数(从东家开始顺序)
        leftTileCount:剩余牌数
        doras:宝牌列表
        """
        if self.AI_state != State.Playing:
            return True  # AI未准备就绪，停止解析
        self.isLiqi = False
        self.cardRecorder.clear()
        dora136, _ = self.cardRecorder.majsoul2tenhou(doras[0])
        seed = [ju, ben, 0, -1, -1, dora136]     # 当前轮数/连庄立直信息
        self.ten = ten = [scores[(self.mySeat+i) % 4] //
                          100 for i in range(4)]  # 当前分数(1ten=100分)
        oya = (4-self.mySeat+ju) % 4      # 0~3 当前轮谁是庄家(我是0)
        self.hai = []     # 当前手牌tile136
        for tile in tiles[:13]:
            tile136, _ = self.cardRecorder.majsoul2tenhou(tile)
            self.hai.append(tile136)
        assert(len(seed) == 6)
        assert(len(ten) == 4)
        assert(0 <= oya < 4)
        self.send(('<INIT seed="'+','.join(str(i) for i in seed)+'" ten="'+','.join(str(i)
                                                                                    for i in ten)+'" oya="'+str(oya)+'" hai="'+','.join(str(i) for i in self.hai)+'"/>\x00').encode())
        if len(tiles) == 14:
            # operation TODO
            self.iDealTile(self.mySeat, tiles[13], leftTileCount, {}, {})

    def discardTile(self, seat: int, tile: str, moqie: bool, isLiqi: bool, operation):
        """
        seat:打牌的玩家
        tile:打出的手牌
        moqie:是否是摸切
        isLiqi:当回合是否出牌后立直
        operation:可选动作(吃碰杠)
        """
        assert(0 <= seat < 4)
        assert(tile in sdk.all_tiles)
        if isLiqi:
            msg_dict = {'opcode': 'REACH', 'who': (
                seat-self.mySeat) % 4, 'step': 1}
            self.send(self.tenhouEncode(msg_dict))
        op = 'DEFG'[(seat-self.mySeat) % 4]
        if op == 'D' and self.lastOp['opcode'] == 'D':
            tile136 = int(self.lastOp['p'])
            self.hai.remove(tile136)
        else:
            tile136, _ = self.cardRecorder.majsoul2tenhou(tile)
        if moqie and op != 'D':
            op = op.lower()
        msg_dict = {'opcode': op+str(tile136)}
        if operation != None:
            assert(operation.get('seat', 0) == self.mySeat)
            opList = operation.get('operationList', [])
            canChi = any(op['type'] == Operation.Chi.value for op in opList)
            canPeng = any(op['type'] == Operation.Peng.value for op in opList)
            canGang = any(
                op['type'] == Operation.MingGang.value for op in opList)
            canHu = any(op['type'] == Operation.Hu.value for op in opList)
            if canHu:
                msg_dict['t'] = 8
            elif canGang:
                msg_dict['t'] = 3
            elif canPeng:
                msg_dict['t'] = 1
            elif canChi:
                msg_dict['t'] = 4
        self.send(self.tenhouEncode(msg_dict))
        self.lastDiscard = tile136
        self.lastDiscardSeat = seat
        #operation TODO

    def dealTile(self, seat: int, leftTileCount: int, liqi: Dict):
        """
        seat:摸牌的玩家
        leftTileCount:剩余牌数
        liqi:如果上一回合玩家出牌立直，则紧接着的摸牌阶段有此参数表示立直成功
        """
        assert(0 <= seat < 4)
        assert(type(liqi) == dict or liqi == None)
        if liqi:
            tenhow_seat = (liqi.get('seat', 0)-self.mySeat) % 4
            score = liqi.get('score', 0)
            self.ten[tenhow_seat] = score//100
            msg_dict = {'opcode': 'REACH', 'who': tenhow_seat,
                        'ten': ','.join(str(i) for i in self.ten), 'step': 2}
            self.send(self.tenhouEncode(msg_dict))
        op = 'UVW'[(seat-self.mySeat-1) % 4]
        self.send(('<'+op+'/>\x00').encode())

    def iDealTile(self, seat: int, tile: str, leftTileCount: int, liqi: Dict, operation: Dict):
        """
        seat:我自己
        tile:摸到的牌
        leftTileCount:剩余牌数
        liqi:如果上一回合玩家出牌立直，则紧接着的摸牌阶段有此参数表示立直成功
        operation:可选操作列表
        """
        assert(seat == self.mySeat)
        assert(tile in sdk.all_tiles)
        assert(type(liqi) == dict or liqi == None)
        if liqi:
            tenhow_seat = (liqi.get('seat', 0)-self.mySeat) % 4
            score = liqi.get('score', 0)
            self.ten[tenhow_seat] = score//100
            msg_dict = {'opcode': 'REACH', 'who': tenhow_seat,
                        'ten': ','.join(str(i) for i in self.ten), 'step': 2}
            self.send(self.tenhouEncode(msg_dict))
        tile136, _ = self.cardRecorder.majsoul2tenhou(tile)
        self.hai.append(tile136)
        msg_dict = {'opcode': 'T'+str(tile136)}
        if operation != None:
            opList = operation.get('operationList', [])
            canLiqi = any(op['type'] == Operation.Liqi.value for op in opList)
            canZimo = any(op['type'] == Operation.Zimo.value for op in opList)
            canHu = any(op['type'] == Operation.Hu.value for op in opList)
            if canZimo or canHu:
                msg_dict['t'] = 16  # 自摸
            elif canLiqi:
                msg_dict['t'] = 32  # 立直
        self.send(self.tenhouEncode(msg_dict))

    def chiPengGang(self, type_: int, seat: int, tiles: List[str], froms: List[int], tileStates: List[int]):
        """
        type_:操作类型
        seat:吃碰杠的玩家
        tiles:吃碰杠牌组
        froms:每张牌来自哪个玩家
        tileStates:未知(TODO)
        """
        # 加杠实现
        assert(0 <= seat < 4)
        assert(all(tile in sdk.all_tiles for tile in tiles))
        assert(all(0 <= i < 4 for i in froms))
        assert(seat != froms[-1])
        lastDiscardStr = self.cardRecorder.tenhou2majsoul(
            tile136=self.lastDiscard)
        assert(tiles[-1] == lastDiscardStr)
        tenhou_seat = (seat-self.mySeat) % 4
        from_whom = (froms[-1]-seat) % 4

        def popHai(tile):
            #从self.hai中找到tile并pop
            for tile136 in self.hai:
                if self.cardRecorder.tenhou2majsoul(tile136=tile136) == tile:
                    self.hai.remove(tile136)
                    return tile136
            raise Exception(tile+' not found.')

        if type_ in (0, 1):
            assert(len(tiles) == 3)
            tile1 = self.lastDiscard
            if seat == self.mySeat:
                tile2 = popHai(tiles[1])
                tile3 = popHai(tiles[0])
            else:
                tile2 = self.cardRecorder.majsoul2tenhou(tiles[1])[0]
                tile3 = self.cardRecorder.majsoul2tenhou(tiles[0])[0]
            tile136s = sorted([tile1, tile2, tile3])
            t1, t2, t3 = (i % 4 for i in tile136s)
            if type_ == 0:
                # 吃
                assert(tiles[0] != tiles[1] != tiles[2])
                base = tile136s[0]//4  # 最小牌tile34
                base = base//9*7 + base % 9
                called = tile136s.index(tile1)  # 哪张牌是别人的
                base_and_called = base*3 + called
                m = (base_and_called << 10) + (t3 << 7) + \
                    (t2 << 5) + (t1 << 3) + (1 << 2) + from_whom
            elif type_ == 1:
                # 碰
                assert(tiles[0] == tiles[1] == tiles[2] or all(
                    i[0] in ('0', '5') for i in tiles))
                base = tile136s[0]//4  # 最小牌tile34
                called = tile136s.index(tile1)  # 哪张牌是别人的
                base_and_called = base*3 + called
                t4 = ((1, 2, 3), (0, 2, 3), (0, 1, 3),
                      (0, 1, 2)).index((t1, t2, t3))
                m = (base_and_called << 9) + (t4 << 5) + (1 << 3) + from_whom
        elif type_ == 2:
            # 明杠
            assert(len(tiles) == 4)
            assert(tiles[0] == tiles[1] == tiles[2] == tiles[2] or all(
                i[0] in ('0', '5') for i in tiles))
            tile1 = self.lastDiscard
            if seat == self.mySeat:
                tile2 = popHai(tiles[2])
                tile3 = popHai(tiles[1])
                tile4 = popHai(tiles[0])
            else:
                tile2 = self.cardRecorder.majsoul2tenhou(tiles[2])[0]
                tile3 = self.cardRecorder.majsoul2tenhou(tiles[1])[0]
                tile4 = self.cardRecorder.majsoul2tenhou(tiles[0])[0]
            tile136s = sorted([tile1, tile2, tile3, tile4])
            base = tile136s[0]//4  # 最小牌tile34
            called = tile136s.index(tile1)  # 哪张牌是别人的
            base_and_called = base*4 + called
            m = (base_and_called << 8) + (1 << 6) + from_whom
        else:
            raise NotImplementedError
        msg_dict = {'opcode': 'N', 'who': tenhou_seat, 'm': m}
        self.send(self.tenhouEncode(msg_dict))

    def hule(self, hand: List[str], huTile: str, seat: int, zimo: bool, liqi: bool, doras: List[str], liDoras: List[str], fan: int, fu: int, oldScores: List[int], deltaScores: List[int], newScores: List[int]):
        """
        hand:胡牌者手牌
        huTile:点炮牌
        seat:玩家座次
        zimo:是否自摸
        liqi:是否立直
        doras:明宝牌列表
        liDoras:里宝牌列表
        fan:番数
        fu:符数
        oldScores:4人旧分
        deltaScores::新分减旧分
        newScores:4人新分
        """
        assert(all(tile in all_tiles for tile in hand))
        assert(huTile in all_tiles)
        assert(0 <= seat < 4)
        assert(all(tile in all_tiles for tile in doras))
        assert(all(tile in all_tiles for tile in liDoras))
        def L2S(l): return ','.join(str(i) for i in l)
        who = (seat-self.mySeat) % 4
        fromWho = who if zimo else (self.lastDiscardSeat-self.mySeat) % 4
        self.cardRecorder.clear()
        machi, _ = self.cardRecorder.majsoul2tenhou(huTile)
        ten = L2S((fu, deltaScores[seat], 0))
        if seat == self.mySeat:
            hai = L2S(self.hai)
        else:
            hai = L2S(self.cardRecorder.majsoul2tenhou(
                tile)[0] for tile in hand)
        doraHai = L2S(self.cardRecorder.majsoul2tenhou(
            tile)[0] for tile in doras)
        doraHaiUra = L2S(self.cardRecorder.majsoul2tenhou(tile)[
                         0] for tile in liDoras)
        sc = []
        for i in range(4):
            sc.append(oldScores[(self.mySeat+i) % 4]//100)
            sc.append(deltaScores[(self.mySeat+i) % 4]//100)
        sc = L2S(sc)
        msg_dict = {'opcode': 'AGARI', 'who': who, 'fromWho': fromWho,
                    'machi': machi, 'ten': ten, 'hai': hai, 'doraHai': doraHai, 'sc': sc}
        if doraHaiUra:
            msg_dict['doraHaiUra'] = doraHaiUra
        self.send(self.tenhouEncode(msg_dict))
        self.AI_state = State.WaitingForStart

    def liuju(self, tingpai: List[bool], hands: List[List[str]], oldScores: List[int], deltaScores: List[int]):
        """
        tingpai:4个玩家是否停牌
        hands:听牌玩家的手牌(没听为[])
        oldScores:4人旧分
        deltaScores::新分减旧分
        """
        assert(all(tile in all_tiles for hand in hands for tile in hand))
        def L2S(l): return ','.join(str(i) for i in l)
        sc = []
        for i in range(4):
            sc.append(oldScores[(self.mySeat+i) % 4]//100)
            sc.append(deltaScores[(self.mySeat+i) % 4]//100)
        msg_dict = {'opcode': 'RYUUKYOKU', 'sc': L2S(sc)}
        self.cardRecorder.clear()
        for i in range(4):
            if tingpai[i]:
                tenhou_seat = (i-self.mySeat) % 4
                if i == self.mySeat:
                    hai = self.hai
                else:
                    hai = [self.cardRecorder.majsoul2tenhou(
                        tile)[0] for tile in hands[i]]
                msg_dict['hai'+str(tenhou_seat)] = L2S(hai)
        self.send(self.tenhouEncode(msg_dict))
        self.AI_state = State.WaitingForStart

    #-------------------------Majsoul动作函数-------------------------

    def on_DiscardTile(self, msg_dict):
        if self.isLiqi:
            return
        time.sleep(0.5)
        self.lastOp = msg_dict
        assert(msg_dict['opcode'] == 'D')
        tile = self.cardRecorder.tenhou2majsoul(tile136=int(msg_dict['p']))
        self.actionDiscardTile(tile)

    def on_ChiPengGang(self, msg_dict):
        # <N ...\>
        time.sleep(1)
        if 'type' not in msg_dict:
            #无操作
            self.actionChiPengGang(sdk.Operation.NoEffect, [])
            return
        type_ = int(msg_dict['type'])
        if type_ == 1:
            #碰
            tile1 = self.cardRecorder.tenhou2majsoul(tile136=msg_dict['hai0'])
            tile2 = self.cardRecorder.tenhou2majsoul(tile136=msg_dict['hai1'])
            self.actionChiPengGang(sdk.Operation.Peng, [tile1, tile2])
        elif type_ == 3:
            #吃
            tile1 = self.cardRecorder.tenhou2majsoul(tile136=msg_dict['hai0'])
            tile2 = self.cardRecorder.tenhou2majsoul(tile136=msg_dict['hai1'])
            self.actionChiPengGang(sdk.Operation.Chi, [tile1, tile2])
        elif type_ == 6:
            #点炮胡
            self.actionHu()
        elif type_ == 7:
            #自摸胡
            self.actionZimo()
        else:
            raise NotImplementedError

    def on_Liqi(self, msg_dict):
        time.sleep(1)
        self.isLiqi = True
        tile136 = int(msg_dict['hai'])
        tile = self.cardRecorder.tenhou2majsoul(tile136=tile136)
        self.actionLiqi(tile)


def MainLoop():
    # calibrate browser position
    aiWrapper = AIWrapper()
    print('waiting to calibrate the browser location')
    while not aiWrapper.calibrateMenu():
        print('  majsoul menu not found, calibrate again')
        time.sleep(3)
    # create AI
    print('create AI subprocess')
    AI = Popen('python gui_main.py --fake', cwd='JianYangAI',
               creationflags=CREATE_NEW_CONSOLE)
    # create server
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_address = ('127.0.0.1', 7479)
    print('starting up on %s port %s' % server_address)
    server.bind(server_address)
    server.listen(1)

    while True:
        print('waiting for the AI')
        connection, client_address = server.accept()
        print('AI connection: ', type(connection), connection, client_address)
        aiWrapper.init(connection)

        inputs = [connection]
        outputs = []

        print('waiting for the game to start')
        while not aiWrapper.isPlaying():
            time.sleep(3)

        while True:
            readable, writable, exceptional = select.select(
                inputs, outputs, inputs, 0.1)
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
                connection.close()
                AI.kill()
                break
            aiWrapper.recvFromMajsoul()


if __name__ == '__main__':
    MainLoop()
