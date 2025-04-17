# %%
# Imports #


import os
import platform
import subprocess
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import yaml
from dotenv import load_dotenv

from config import grandparent_dir, parent_dir  # noqa: F401
from utils.display_tools import (  # noqa: F401
    pprint_df,
    pprint_dict,
    pprint_ls,
    print_logger,
)

# %%
# Variables #


DOTENV_PATH = os.path.join(parent_dir, ".env")
if os.path.exists(DOTENV_PATH):
    load_dotenv(DOTENV_PATH)

PLEX_SERVER = os.environ["PLEX_SERVER"]
TOKEN = os.environ["PLEX_TOKEN"]


dict_cache = {}

# %%
# Plex API #


def get_dict_plex_section_numbers(force_refresh=False):
    """
    Get a dictionary of the plex section numbers in the format:
    {
        "Movies": "1",
        "TV Shows": "2",
        ...
    }
    """
    key = "plex_section_numbers"
    if key in dict_cache and not force_refresh:
        return dict_cache[key].copy()

    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    response = requests.get(f"{PLEX_SERVER}/library/sections", headers=headers)
    if response.status_code != 200:
        return {}
    sections_data = response.json()
    dict_section_ids = {}

    for section_data in sections_data["MediaContainer"]["Directory"]:
        dict_section_ids[section_data["title"]] = section_data["key"]

    dict_cache[key] = dict_section_ids.copy()

    return dict_section_ids


# print_logger("Plex section numbers:")
# pprint_dict(get_dict_plex_section_numbers())


def get_dict_plex_movie_data(force_update=False):
    key = "plex_movie_data"
    if key in dict_cache and not force_update:
        return dict_cache[key].copy()

    dict_sections = get_dict_plex_section_numbers()
    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    response = requests.get(
        f"{PLEX_SERVER}/library/sections/{dict_sections['Movies']}/all", headers=headers
    )
    if response.status_code != 200:
        return {}, {}

    movies_data = response.json()

    dict_cache[key] = movies_data.copy()

    return movies_data


# print_logger("Plex movie data:")
# pprint_dict(get_dict_plex_movie_data())


def get_dict_plex_show_data(force_update=False):
    key = "plex_show_data"
    if key in dict_cache and not force_update:
        return dict_cache[key].copy()

    dict_sections = get_dict_plex_section_numbers()
    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    all_seasons_response = requests.get(
        f"{PLEX_SERVER}/library/sections/{dict_sections['TV Shows']}/all",
        headers=headers,
    )
    if all_seasons_response.status_code != 200:
        return {}, {}

    shows_data = all_seasons_response.json()

    dict_cache[key] = shows_data.copy()

    return shows_data


# print_logger("Plex show data:")
# pprint_dict(get_dict_plex_show_data())


def get_seasons_data_for_show_id(show_id, force_update=False):
    key = f"plex_show_data_{show_id}"
    if key in dict_cache and not force_update:
        return dict_cache[key].copy()

    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    all_seasons_response = requests.get(
        f"{PLEX_SERVER}/library/metadata/{show_id}/children",
        headers=headers,
    )
    if all_seasons_response.status_code != 200:
        raise Exception(
            f"Error getting children for show_id {show_id}: {all_seasons_response.status_code}"
        )
    all_seasons_data = (
        all_seasons_response.json().get("MediaContainer", {}).get("Metadata", [])
    )

    dict_cache[key] = all_seasons_data.copy()

    return all_seasons_data


# print_logger("Plex seasons data:")
# pprint_dict(get_seasons_data_for_show_id("31579"))


def get_episode_data_for_season_key(season_key, force_update=False):
    key = f"plex_season_data_{season_key}"
    if key in dict_cache and not force_update:
        return dict_cache[key].copy()

    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    all_episodes_response = requests.get(
        f"{PLEX_SERVER}/library/metadata/{season_key}/children",
        headers=headers,
    )
    if all_episodes_response.status_code != 200:
        raise Exception(
            f"Error getting children for season_key {season_key}: {all_episodes_response.status_code}"
        )
    all_episodes_data = (
        all_episodes_response.json().get("MediaContainer").get("Metadata", [])
    )

    dict_cache[key] = all_episodes_data.copy()

    return all_episodes_data


# print_logger("Plex single season data:")
# pprint_dict(get_apisode_data_for_season_key("31616"))


# %%
