#!/bin/bash

# todo: this should be copied instead of cloned
git clone --depth 1 https://github.com/ucl-cs-diamant/diamant_game_interface
/bin/python3 -m venv venv --system-site-packages
source venv/bin/activate

if test -f "requirements.txt"; then
  python3 -m pip install -r requirements.txt
fi
#echo "STARTING PLAYER $player_id"
python3 main.py
