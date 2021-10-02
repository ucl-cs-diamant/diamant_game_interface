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
    def __init__(self, server_address, server_port=80):
        self.players = []
        self.players_code_directories = {}
        self.player_processes = {}
        self.game_id = None
        self.server_address = server_address
        self.server_port = server_port
        self.player_communication_channel = None
        self.ready = False
        self.fetch_match_retry_interval: float = float(os.environ.get("RETRY_INTERVAL", 1.0))

    def init_game(self):
        self.__fetch_match_data()

        if not self.__prepare_player_code():
            raise RuntimeError("Unable to get player code")  # handle game abortion sometime soon

    async def init_players(self):
        await self.__start_socket_server()  # make sure socket server is ready before spawning players
        self.__launch_players()
        if self.check_dead_players():
            raise RuntimeError("One or more player died")  # handle game abortion sometime soon
        await self.player_communication_channel.wait_until_players_connected(len(self.players))

        self.ready = True

    def __fetch_match_data(self):
        attempts = 5
        while True:  # PEP315
            try:
                res = requests.get(f"http://{self.server_address}:{self.server_port}/request_match/")
                if res.status_code == 200:
                    match_data = res.json()
                    self.players = match_data["players"]
                    self.game_id = match_data["game_id"]
                    break
            except requests.ConnectionError:
                attempts -= 1
                if attempts > 0:
                    continue
                raise ValueError(f"Unable to connect to {self.server_address}:{self.server_port}")
            finally:
                time.sleep(self.fetch_match_retry_interval)  # make this configurable in the future

    def __fetch_player_code(self, player_id: int):
        # self.players_code_directories[player_id] = ''
        url = f"http://{self.server_address}:{self.server_port}/code_list/{player_id}/download/"
        with requests.get(url) as res:
            try:
                res.raise_for_status()
                if res.status_code == 200:
                    player_code_dir = tempfile.TemporaryDirectory(dir='/dev/shm')
                    with tempfile.TemporaryFile() as tf:
                        tf.write(res.content)
                        tf.seek(0)
                        tar = tarfile.open(fileobj=tf)
                        tar.extractall(path=player_code_dir.name)
                    self.players_code_directories[player_id] = player_code_dir
                    return
                raise requests.exceptions.RequestException
            except requests.exceptions as e:
                raise RuntimeError(e)

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
            shutil.copy2("diamant_game_interface/start_player.sh",
                         os.path.join(self.players_code_directories[player_id].name, "start_player.sh"))
            # TODO: move git clone into the engine interface/backend. That will save bandwidth and time
            self.player_processes[player_id] = subprocess.Popen(['/bin/bash', './start_player.sh'],
                                                                cwd=self.players_code_directories[player_id].name,
                                                                env={'player_id': str(player_id)})

        # check for players that exited prematurely, terminate game if players exited/crashed

    async def __start_socket_server(self):
        self.player_communication_channel = PlayerCommunication()
        await self.player_communication_channel.start_socket_server()

    def check_dead_players(self):
        dead_players = []
        for player_id in self.player_processes:
            if self.player_processes[player_id].poll() is not None:
                dead_players.append(player_id)
        return dead_players

    """
    Player side of the interface will answer with a simple json: {"decision": <True/False>}
    """

    async def request_decisions(self, game_state):
        if not self.ready:
            await self.init_players()

        if self.check_dead_players():
            raise RuntimeError("One or more player died")  # handle game abortion sometime soon

        await self.player_communication_channel.broadcast_decision_request(game_state)
        return await self.player_communication_channel.receive_player_decisions()

    def report_outcome(self, winning_players: list, match_history: list):
        url = f"http://{self.server_address}:{self.server_port}/matches/{self.game_id}/report_match/"
        _ = requests.post(url, json={'outcome': 'ok', 'winners': winning_players, 'match_history': match_history})
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

    async def __receive_msg(self, player_id: int = None, reader_obj: asyncio.StreamReader = None):
        reader: asyncio.StreamReader = self.player_comm_channels[player_id][0] if reader_obj is None else reader_obj

        bytes_buffer = bytearray()
        bytes_read = 0
        while bytes_read < 4:
            data = await reader.read(5)
            bytes_read += len(data)
            bytes_buffer.extend(data)
        message_length = int.from_bytes(bytes_buffer[:4], "big") + 4

        while bytes_read < message_length:
            data = await reader.read(message_length - bytes_read)  # todo: use readexactly
            bytes_read += len(data)
            bytes_buffer.extend(data)

        return json.loads(bytes_buffer[4:].decode('utf-8'))

    async def __send_message(self, player_id, message: dict):
        message = json.dumps(message)
        encoded_message = bytearray(message.encode('utf-8'))
        message_length = len(encoded_message)
        encoded_message[0:0] = message_length.to_bytes(4, byteorder='big')

        writer: asyncio.StreamWriter = self.player_comm_channels[player_id][1]  # clean this up
        writer.write(encoded_message)
        await self.player_comm_channels[player_id][1].drain()

    async def __send_decision_request(self, player_id, game_state):
        # self.player_comm_channels[player_id][1].write("PUT SOMETHING RELEVANT HERE")
        # await self.player_comm_channels[player_id][1].drain()
        await self.__send_message(player_id, {"game_state": game_state})

    async def broadcast_decision_request(self, game_state):
        await asyncio.gather(*[self.__send_decision_request(player_id, game_state)
                               for player_id in self.player_comm_channels.keys()])

        # todo: put in the logic for reading their response

    async def receive_player_decisions(self):
        players = self.player_comm_channels.keys()
        decisions = await asyncio.gather(*[self.__receive_msg(player_id)
                                           for player_id in players])
        decisions = {player_id: decisions[i] for i, player_id in enumerate(players)}
        return decisions

    async def wait_until_players_connected(self, player_count):
        while len(self.player_comm_channels.keys()) < player_count:
            await asyncio.sleep(0.05)

    async def __client_connected_cb(self, reader, writer):
        # print(self.sock_address, " - client connected")

        client_identification = await self.__receive_msg(reader_obj=reader)
        print(f"{client_identification['player_id']} connected")
        self.player_comm_channels[client_identification['player_id']] = (reader, writer)

    async def start_socket_server(self):
        await asyncio.start_unix_server(self.__client_connected_cb, path=self.sock_address)
