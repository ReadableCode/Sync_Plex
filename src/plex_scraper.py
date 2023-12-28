# %%
## Imports ##


if __name__ != "__main__":
    print(f"Importing {__name__}")

import time
import requests
import xml.etree.ElementTree as ET
import os
import sys
from dotenv import load_dotenv
import shutil
import subprocess

# append grandparent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from utils.display_tools import pprint_dict, pprint_ls, print_logger

from config import (
    file_dir,
    parent_dir,
    grandparent_dir,
    great_grandparent_dir,
    data_dir,
)


# %%
## Variables ##


operating_system = "Windows" if os.name == "nt" else "Linux"
dotenv_path = os.path.join(grandparent_dir, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

PLEX_SERVER = os.environ["PLEX_SERVER"]
TOKEN = os.environ["PLEX_TOKEN"]
tv_shows_section_id = "2"

dict_shows_to_watch = {
    "American Dad!": 6,
    "Hemlock Grove": 1,
    "The Great": 3,
    "Lucifer": 3,
    "Marvel's Luke Cage": 3,
    "New Girl": 3,
    "Fullmetal Alchemist: Brotherhood": 6,
    "The Boys": 3,
    "Loki": 5,
    "House": 3,
    "House of the Dragon": 2,
    "Mr. Robot": 3,
}

ls_movies_to_watch = [
    "Zootopia",
    "Ready Player One",
    "Home",
    "Twilight",
    "Encanto",
    "Moana",
    "Inside Out",
    "Frozen",
    "Frozen II",
    "Big Hero 6",
]


# %%
## Path Functions ##


def get_mapped_source_path_path():
    if operating_system == "Windows":
        return "\\\\192.168.86.31\\Media"
    elif operating_system == "Linux":
        return "/mnt/192.168.86.31/Media"  # TODO fix this on a system with a mount
    else:
        raise Exception("Operating system not recognized")


def get_destination_path():
    if operating_system == "Windows":
        return os.path.join("I:\\", "Media")
    elif operating_system == "Linux":
        return "/home/james/Downloads"  # TODO fix this on a system with a mount
    else:
        raise Exception("Operating system not recognized")


# %%
## Get Shows ##


def get_plex_sections():
    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    response = requests.get(f"{PLEX_SERVER}/library/sections", headers=headers)
    if response.status_code != 200:
        return {}
    sections_data = response.json()
    dict_section_ids = {}

    for section_data in sections_data["MediaContainer"]["Directory"]:
        dict_section_ids[section_data["title"]] = section_data["key"]

    return dict_section_ids


def get_movies():
    dict_sections = get_plex_sections()
    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    response = requests.get(
        f"{PLEX_SERVER}/library/sections/{dict_sections['Movies']}/all", headers=headers
    )
    if response.status_code != 200:
        return {}, {}

    movies_data = response.json()

    dict_all_movies = {}
    dict_watch_movies = {}

    for movie in movies_data["MediaContainer"]["Metadata"]:
        movie_title = movie["title"]
        movie_id = movie["ratingKey"]

        dict_all_movies.setdefault(movie_title, {})["movie_id"] = movie_id

        for watch_movie in ls_movies_to_watch:
            if watch_movie == movie_title.split(" (")[0]:
                dict_watch_movies.setdefault(watch_movie, {})["movie_id"] = movie_id

    return dict_all_movies, dict_watch_movies


def get_shows():
    dict_sections = get_plex_sections()
    headers = {"X-Plex-Token": TOKEN, "Accept": "application/json"}
    response = requests.get(
        f"{PLEX_SERVER}/library/sections/{dict_sections['TV Shows']}/all",
        headers=headers,
    )
    if response.status_code != 200:
        return {}, {}

    shows_data = response.json()

    dict_all_shows = {}
    dict_watch_shows = {}

    for show in shows_data["MediaContainer"]["Metadata"]:
        show_title = show["title"]
        show_id = show["ratingKey"]

        dict_all_shows.setdefault(show_title, {})["show_id"] = show_id

        for watch_show in dict_shows_to_watch.keys():
            if watch_show == show_title:
                dict_watch_shows.setdefault(watch_show, {})["show_id"] = show_id

    return dict_all_shows, dict_watch_shows


# %%
## Imports ##


def get_file_paths_next_x_episodes_of_show(show_title, num_episodes):
    dict_all_shows, dict_watch_shows = get_shows()

    show_id = dict_watch_shows[show_title]["show_id"]
    response = requests.get(
        f"{PLEX_SERVER}/library/metadata/{show_id}/allLeaves",
        params={"X-Plex-Token": TOKEN},
    )

    root = ET.fromstring(response.content)

    ls_file_paths = []
    for episode in root.findall(".//Video"):
        episode_title = episode.get("title")
        episode_server_path = episode.find(".//Part").get("file")
        episode_norm_path = os.path.normpath(episode_server_path)
        episode_mapped_path = episode_norm_path.replace(
            "\\data", get_mapped_source_path_path()
        )
        episode_num_views = episode.get("viewCount", 0)

        if episode_num_views == 0:
            ls_file_paths.append(episode_mapped_path)
            if len(ls_file_paths) == num_episodes:
                break

    return ls_file_paths


def get_file_paths_movies():
    dict_all_movies, dict_watch_movies = get_movies()

    ls_file_paths = []
    for movie in dict_watch_movies.keys():
        movie_id = dict_watch_movies[movie]["movie_id"]
        response = requests.get(
            f"{PLEX_SERVER}/library/metadata/{movie_id}",
            params={"X-Plex-Token": TOKEN},
        )

        root = ET.fromstring(response.content)
        for video in root.findall(".//Video"):
            video_title = video.get("title")
            video_server_path = video.find(".//Part").get("file")
            video_norm_path = os.path.normpath(video_server_path)
            video_mapped_path = video_norm_path.replace(
                "\\data", get_mapped_source_path_path()
            )
            ls_file_paths.append(video_mapped_path)

    return ls_file_paths


def get_list_download_tasks():
    ls_file_paths_to_download = []
    for show_title, num_episodes in dict_shows_to_watch.items():
        ls_file_paths_this_show = get_file_paths_next_x_episodes_of_show(
            show_title, num_episodes
        )
        ls_file_paths_to_download.extend(ls_file_paths_this_show)
    ls_file_paths_to_download.extend(get_file_paths_movies())

    ls_tasks = []
    for file_path in ls_file_paths_to_download:
        destination_path = file_path.replace(
            get_mapped_source_path_path(), get_destination_path()
        )
        ls_tasks.append((file_path, destination_path))

    return ls_tasks


def download_files(dry_run=False):
    ls_tasks = get_list_download_tasks()

    ls_files_to_skip = []
    size_of_skip = 0
    # if dry run, initilize ls files to copy, ls_files to skip, size of copy, and size of skip
    if dry_run:
        dict_files_to_copy = {}
        size_of_copy = 0

    if os.path.exists(
        os.path.join(get_destination_path(), "plex_downloader_target.txt")
    ):
        print("plex_downloader_target exists")
    else:
        print("target location doesnt exist")
        raise Exception("Target location doesnt exist")

    for task in ls_tasks:
        source_path = task[0]
        destination_file = task[1]
        destination_dir = os.path.dirname(destination_file)
        file_size = os.path.getsize(source_path)
        base_name = os.path.basename(source_path)

        print("#" * 50)
        print(f"Working on file: {base_name}")

        if os.path.exists(source_path):
            file_already_present = os.path.exists(destination_file)
            if file_already_present:
                print(
                    f"File {os.path.basename(destination_file)} already present, skipping, source size: {file_size}"
                )
                ls_files_to_skip.append(base_name)
                size_of_skip += file_size
            elif dry_run:
                print(
                    f"Dry run: would use {'robocopy' if operating_system == 'Windows' else 'rsync'} to copy {source_path} to {destination_dir}, size: {file_size}"
                )
                dict_files_to_copy[base_name] = f"{file_size / 1e9} GB"
                size_of_copy += file_size
            else:
                if not os.path.exists(destination_dir):
                    os.makedirs(destination_dir)
                if operating_system == "Windows":
                    print(
                        f"Using robocopy to copy {source_path} to {destination_dir}, size: {file_size}"
                    )
                    subprocess.run(
                        [
                            "robocopy",
                            os.path.dirname(source_path),
                            destination_dir,
                            base_name,
                        ]
                    )
                elif operating_system == "Linux":
                    print(
                        f"Using rsync to copy {source_path} to {destination_dir}, size: {file_size}"
                    )
                    subprocess.run(["rsync", "-av", source_path, destination_dir])
                else:
                    raise Exception("Operating system not recognized")

        else:
            print(f"Source path {source_path} does not exist")

    if dry_run:
        print("#" * 50)
        print("Dry Run Summary:")
        print_logger("Files to copy:")
        pprint_dict(dict_files_to_copy)
        print(f"Files to copy: {len(dict_files_to_copy)}")
        print(f"Size of files to copy: {size_of_copy / 1e9} GB")
        print(f"Files to skip: {len(ls_files_to_skip)}")
        print(f"Size of files to skip: {size_of_skip / 1e9} GB")


# %%
## Main ##


if __name__ == "__main__":
    start_time = time.time()
    download_files(dry_run=True)
    end_time = time.time()

    print_logger(f"Time taken: {end_time - start_time} seconds", as_break=True)


# %%
