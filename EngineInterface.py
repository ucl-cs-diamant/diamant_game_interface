# from collections.abc import Callable
import asyncio
import json
import os

import requests
import time
import concurrent.futures
import tempfile
import tarfile
import subprocess
import shutil


# todo: deal with http/https later
# noinspection HttpUrlsUsage
class EngineInterface:
    def __init__(self, server_address, server_port=80, ready_callback=None):
        self.players = []
        self.players_code_directories = {}
        self.player_processes = {}
        self.game_id = None
        self.server_address = server_address
        self.server_port = server_port
        self.player_communication_channel = None
        self.ready = False
        # self.ready_callback = ready_callback

    # def set_ready_callback(self, ready_callback: Callable):
    #     assert callable(ready_callback)
    #     self.ready_callback = ready_callback

    def __fetch_match_data(self):
        while True:  # PEP315
            try:
                res = requests.get(f"http://{self.server_address}:{self.server_port}/requestMatch")
            except requests.ConnectionError:
                raise ValueError(f"{self.server_address}:{self.server_port} is not a valid address")
            if res.status_code == 200:
                break
            time.sleep(0.2)
        match_data = res.json()
        self.players = match_data["players"]  # todo: check against the actual API. I don't remember what it's like
        self.game_id = match_data["game_id"]

    def __fetch_player_code(self, player_id):
        # self.players_code_directories[player_id] = ''
        url = f"http://{self.server_address}:{self.server_port}/users/{player_id}/latest_code/"
        with requests.get(url) as res:
            res.raise_for_status()
            if res.status_code == 200:
                player_code_dir = tempfile.TemporaryDirectory()
                with tempfile.TemporaryFile() as tf:
                    tf.write(res.content)
                    tar = tarfile.open(fileobj=tf)
                    tar.extractall(path=player_code_dir.name)
                self.players_code_directories[player_id] = player_code_dir
                return
            raise requests.exceptions.RequestException

    def __prepare_player_code(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            fetch_player_code_futures = [executor.submit(self.__fetch_player_code, player_id)
                                         for player_id in self.players]
            # concurrent.futures.wait(fetch_player_code_futures)
            for future in concurrent.futures.as_completed(fetch_player_code_futures):
                if future.exception() is not None:
                    return False
        return True

    def __launch_players(self):
        for player_id in self.players:
            shutil.copy2("start_player.sh", os.path.join(self.players_code_directories[player_id], "start_player.sh"))
            self.player_processes[player_id] = subprocess.Popen(['/bin/bash', './start_player.sh'],
                                                                cwd=self.players_code_directories[player_id],
                                                                env={'player_id': player_id})
        # check for players that exited prematurely, terminate game if players exited/crashed

    async def __start_socket_server(self):
        self.player_communication_channel = PlayerCommunication()
        await self.player_communication_channel.start_socket_server()

    async def init_game(self):
        self.__fetch_match_data()
        if not self.__prepare_player_code():
            pass
            # handle game abortion
        await self.__start_socket_server()  # todo: make sure socket server is ready before spawning players
        self.__launch_players()

        self.ready = True

    """
    Player side of the interface will answer with a simple json: {"decision": <True/False>}
    """
    async def request_decisions(self):
        await self.player_communication_channel.broadcast_decision_request()
        decisions = await asyncio.gather(self.player_communication_channel.__receive_msg(player_id)
                                         for player_id in self.players)
        decisions = {player_id: decisions[i] for i, player_id in enumerate(self.players)}
        return decisions

    def report_outcome(self, winning_players):
        url = f"http://{self.server_address}:{self.server_port}/report_outcome/"
        _ = requests.post(url, data={'outcome': 'ok', 'winners': winning_players})
        # todo: implement error checking
        exit(0)


class PlayerCommunication:
    def __init__(self):
        self.sock_address = '/tmp/game.sock'
        self.player_comm_channels = {}

        try:
            os.unlink(self.sock_address)
        except OSError:
            if os.path.exists(self.sock_address):
                raise

    async def __receive_msg(self, player_id=None, reader_obj=None):
        reader = self.player_comm_channels[player_id][0] if reader_obj is None else reader_obj

        bytes_buffer = bytearray()
        bytes_read = 0
        while bytes_read < 4:
            data = await reader.recv(1024)
            bytes_read += len(data)
            bytes_buffer.extend(data)
        message_length = int.from_bytes(bytes_buffer[:4], "big")

        while bytes_read < message_length:
            data = await reader.recv(message_length - bytes_read)
            bytes_read += len(data)
            bytes_buffer.extend(data)

        return json.loads(bytes_buffer[4:].decode('utf-8'))

    async def __send_message(self, player_id, message: dict):
        message = json.dumps(message)
        encoded_message = bytearray(message.encode('utf-8'))
        message_length = len(encoded_message)
        encoded_message[0:0] = message_length.to_bytes(4, byteorder='big')
        self.player_comm_channels[player_id][1].write(encoded_message)
        await self.player_comm_channels[player_id][1].drain()

    async def __send_decision_request(self, player_id):
        # self.player_comm_channels[player_id][1].write("PUT SOMETHING RELEVANT HERE")
        # await self.player_comm_channels[player_id][1].drain()
        await self.__send_message(player_id, {"game_state": {'yep': True}})

    async def broadcast_decision_request(self):
        await asyncio.gather(self.__send_decision_request(player_id)
                             for player_id in self.player_comm_channels.keys())

        # todo: put in the logic for reading their response

    async def __client_connected_cb(self, reader, writer):
        print(self.sock_address, " - client connected")

        client_identification = await self.__receive_msg(reader_obj=reader)

        self.player_comm_channels[client_identification['player_id']] = (reader, writer)

    async def start_socket_server(self):
        await asyncio.start_unix_server(self.__client_connected_cb, path=self.sock_address)
