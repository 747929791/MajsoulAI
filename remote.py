# -*- coding: utf-8 -*-
# Reverse proxy deployed on remote AI server.
# Listen to client requests and call AI locally.
import time
import select
import socket
import subprocess

# for local AI
AI_HOST = '127.0.0.1'
AI_PORT = 7479

# for remote client
REMOTE_HOST = '0.0.0.0'
REMOTE_PORT = 14782


def GameLoop(client_conn, AI, AI_conn):
    inputs = [client_conn, AI_conn]
    outputs = []
    while True:
        readable, writable, exceptional = select.select(
            inputs, outputs, inputs, 5)
        for s in readable:
            data = s.recv(1024)
            if data:
                # A readable client socket has data
                if s == AI_conn:
                    client_conn.send(data)
                else:
                    AI_conn.send(data)
            else:
                # Interpret empty result as closed connection
                print('closing server after reading no data')
                AI_conn.close()
                client_conn.close()
                AI.kill()
                return
        # Handle "exceptional conditions"
        for s in exceptional:
            print('handling exceptional condition for', s.getpeername())
            AI_conn.close()
            client_conn.close()
            AI.kill()
            return


if __name__ == '__main__':
    remote_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    remote_server_address = (REMOTE_HOST, REMOTE_PORT)
    print('remote server starting up on %s port %s' % remote_server_address)
    remote_server.bind(remote_server_address)
    remote_server.listen(1)

    AI_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    AI_server_address = (AI_HOST, AI_PORT)
    print('AI server starting up on %s port %s' % AI_server_address)
    AI_server.bind(AI_server_address)
    AI_server.listen(1)

    while True:
        print('wating for client.')
        client_conn, client_address = remote_server.accept()
        print('client connection: ', client_conn, client_address)
        AI = subprocess.Popen('python3 main.py --fake',
                              shell=True, cwd='JianYangAI')
        AI_conn, AI_address = AI_server.accept()
        print('AI connection: ', AI_conn, AI_address)
        client_conn.send(b'ACK')
        print('AI is ready.')

        GameLoop(client_conn, AI, AI_conn)
