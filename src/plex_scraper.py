# %%
# Imports #

import os
import platform
import subprocess
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from dotenv import load_dotenv

from config import grandparent_dir
from utils.display_tools import (  # noqa: F401
    pprint_df,
    pprint_dict,
    pprint_ls,
    print_logger,
)

# %%
# Variables #


operating_system = "Windows" if os.name == "nt" else "Linux"
dotenv_path = os.path.join(grandparent_dir, ".env")
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path)

PLEX_SERVER = os.environ["PLEX_SERVER"]
TOKEN = os.environ["PLEX_TOKEN"]

dict_shows_to_watch = {
    "American Dad!": 35,
    "Reacher": 6,
    "The Great": 3,
    "Lucifer": 5,
    "New Girl": 5,
    "Fullmetal Alchemist: Brotherhood": 10,
    "The Boys": 5,
    "House": 5,
    "Brooklyn Nine-Nine": 8,
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
    "Jexi",
    "Her",
]


# %%
# Path Functions #


def get_source_root_path():
    if operating_system == "Windows":
        return "\\\\192.168.86.31\\Media"
    elif operating_system == "Linux":
        return "/mnt/192.168.86.31/Media"  # TODO fix this on a system with a mount
    else:
        raise Exception("Operating system not recognized")


def get_destination_root_path():
    system = platform.system()

    if system == "Windows":
        return r"I:\Media"  # Windows destination (drive letter)
    elif system == "Linux":
        return "/home/jason/Downloads"  # Adjust if using a mounted storage
    else:
        raise Exception(f"Unsupported operating system: {system}")


def get_dest_path(source_file_path):
    dest_path = source_file_path.replace(
        get_source_root_path(), get_destination_root_path()
    )
    # split to list
    ls_dest_path_split = dest_path.split("\\")
    # loop through list, remove item if starts with "Season "
    for i, item in enumerate(ls_dest_path_split):
        if item.startswith("Season "):
            ls_dest_path_split.pop(i)
            break

    # if on windows add backslack after drive letter
    if operating_system == "Windows":
        ls_dest_path_split[0] = ls_dest_path_split[0] + "\\"

    # join list back to string
    dest_path = os.path.join(*ls_dest_path_split)
    return dest_path


# %%
# Get Shows #


def get_dict_plex_section_numbers():
    """
    Get a dictionary of the plex section numbers in the format:
    {
        "Movies": "1",
        "TV Shows": "2",
        ...
    }
    """
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
    """
    Get dicts of all movies and movies to watch in the format:
    {
        "Movie Title": {
            "movie_id": "12345"
        },
    }
    """
    dict_sections = get_dict_plex_section_numbers()
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
    """
    Get dicts of all shows and shows to watch in the format:
    {
        "Show Title": {
            "show_id": "12345"
        },
    }
    """
    dict_sections = get_dict_plex_section_numbers()
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
# Imports #


def get_ls_source_file_paths_next_x_episodes_of_show(show_title, num_episodes):
    """
    Get the source file paths of the next x episodes of a show located on the plex server
    """
    _, dict_watch_shows = get_shows()

    show_id = dict_watch_shows[show_title]["show_id"]
    response = requests.get(
        f"{PLEX_SERVER}/library/metadata/{show_id}/allLeaves",
        params={"X-Plex-Token": TOKEN},
    )

    root = ET.fromstring(response.content)

    ls_file_paths = []
    for episode in root.findall(".//Video"):
        episode_server_path = episode.find(".//Part").get("file")
        episode_norm_path = os.path.normpath(episode_server_path)
        episode_mapped_path = episode_norm_path.replace(
            "\\data", get_source_root_path()
        )
        episode_num_views = episode.get("viewCount", 0)

        if episode_num_views == 0:
            ls_file_paths.append(episode_mapped_path)
            if len(ls_file_paths) == num_episodes:
                break

    return ls_file_paths


def get_ls_source_file_paths_movies():
    """
    Get the source file paths of the movies located on the plex server
    """
    _, dict_watch_movies = get_movies()

    ls_file_paths = []
    for movie in dict_watch_movies.keys():
        movie_id = dict_watch_movies[movie]["movie_id"]
        response = requests.get(
            f"{PLEX_SERVER}/library/metadata/{movie_id}",
            params={"X-Plex-Token": TOKEN},
        )

        root = ET.fromstring(response.content)
        for video in root.findall(".//Video"):
            video_server_path = video.find(".//Part").get("file")
            video_norm_path = os.path.normpath(video_server_path)
            video_mapped_path = video_norm_path.replace(
                "\\data", get_source_root_path()
            )
            ls_file_paths.append(video_mapped_path)

    return ls_file_paths


def get_list_download_tasks():
    ls_file_paths_to_download = []
    for show_title, num_episodes in dict_shows_to_watch.items():
        ls_file_paths_this_show = get_ls_source_file_paths_next_x_episodes_of_show(
            show_title, num_episodes
        )
        ls_file_paths_to_download.extend(ls_file_paths_this_show)
    ls_file_paths_to_download.extend(get_ls_source_file_paths_movies())

    ls_tasks = []
    for file_path in ls_file_paths_to_download:
        ls_tasks.append((file_path, get_dest_path(file_path)))

    return ls_tasks


def remove_unwanted_files(ls_tasks, dry_run=False):
    ls_desired_files = [task[1] for task in ls_tasks]
    dict_files_to_delete = {}
    size_of_delete = 0
    for clean_dir in ["TV", "Movies"]:
        for root, dirs, files in os.walk(
            os.path.join(get_destination_root_path(), clean_dir)
        ):
            for file in files:
                if os.path.join(root, file) not in ls_desired_files:
                    print(f"Unwanted file: {os.path.join(root, file)}")
                    dict_files_to_delete[f"{file}"] = (
                        f"{os.path.getsize(os.path.join(root, file)) / 1e9:.2f} GB"
                    )
                    size_of_delete += os.path.getsize(os.path.join(root, file))
                    if not dry_run:
                        os.remove(os.path.join(root, file))
    if dry_run:
        print_logger("Unwanted Files:", as_break=True)
        pprint_df(
            pd.DataFrame.from_dict(
                dict_files_to_delete, orient="index", columns=["Size"]
            )
            .reset_index()
            .rename(columns={"index": "File"})
        )
        print_logger(f"Files to delete: {len(dict_files_to_delete)}")
        print_logger(f"Size of files to delete: {size_of_delete / 1e9:.2f} GB")

    if not dry_run:
        # remove empty directories
        for root, dirs, files in os.walk(get_destination_root_path(), topdown=False):
            for dir in dirs:
                if not os.listdir(os.path.join(root, dir)):
                    os.rmdir(os.path.join(root, dir))
    else:
        print_logger(
            "Dry run: would remove empty directories listed below:", as_break=True
        )
        for root, dirs, files in os.walk(get_destination_root_path(), topdown=False):
            for dir in dirs:
                if not os.listdir(os.path.join(root, dir)):
                    print_logger(os.path.join(root, dir))


def download_files(ls_tasks, dry_run=False):
    ls_files_to_skip = []
    size_of_skip = 0
    # if dry run, initilize ls files to copy, ls_files to skip, size of copy, and size of skip
    if dry_run:
        dict_files_to_copy = {}
        size_of_copy = 0

    if os.path.exists(
        os.path.join(get_destination_root_path(), "plex_downloader_target.txt")
    ):
        print_logger("plex_downloader_target exists")
    else:
        print_logger("target location doesnt exist", level="error")
        raise Exception("Target location doesnt exist")

    for task in ls_tasks:
        source_path = task[0]
        destination_file = task[1]
        destination_dir = os.path.dirname(destination_file)
        file_size = os.path.getsize(source_path)
        base_name = os.path.basename(source_path)

        if not os.path.exists(source_path):
            print_logger(f"Source path {source_path} does not exist")
            continue

        file_already_present = os.path.exists(destination_file)
        if file_already_present:
            print_logger(
                f"File {os.path.basename(destination_file)} already present, skipping, source size: {file_size / 1e9:.2f} GB",
                level="debug",
            )
            ls_files_to_skip.append(base_name)
            size_of_skip += file_size
        elif dry_run:
            print_logger(
                f"Dry run: would use {'robocopy' if operating_system == 'Windows' else 'rsync'} to copy {source_path} to {destination_dir}, size: {file_size / 1e9:.2f} GB",
                level="debug",
            )
            dict_files_to_copy[base_name] = f"{file_size / 1e9:.2f} GB"
            size_of_copy += file_size
        else:
            if not os.path.exists(destination_dir):
                os.makedirs(destination_dir)
            if operating_system == "Windows":
                print_logger(
                    f"Using robocopy to copy {source_path} to {destination_dir}, size: {file_size / 1e9:.2f} GB"
                )
                result = subprocess.run(
                    [
                        "robocopy",
                        os.path.dirname(source_path),
                        destination_dir,
                        base_name,
                    ],
                    capture_output=True,  # Capture the output of the command
                    text=True,  # Return the output as a string (Python 3.7+)
                )
                success = result.returncode == 1
                if not success:
                    print_logger(
                        f"Error copying file {source_path} to {destination_dir}, size: {file_size / 1e9:.2f} GB, error: {result}",
                        level="error",
                    )

            elif operating_system == "Linux":
                print_logger(
                    f"Using rsync to copy {source_path} to {destination_dir}, size: {file_size / 1e9:.2f} GB"
                )
                subprocess.run(["rsync", "-av", source_path, destination_dir])
            else:
                raise Exception("Operating system not recognized")

    if dry_run:
        print_logger("Dry Run Summary:", as_break=True)
        print_logger("Files to copy:")
        pprint_df(
            pd.DataFrame.from_dict(dict_files_to_copy, orient="index", columns=["Size"])
            .reset_index()
            .rename(columns={"index": "File"})
        )
        print_logger(f"Files to copy: {len(dict_files_to_copy)}")
        print_logger(f"Size of files to copy: {size_of_copy / 1e9:.2f} GB")
        print_logger(f"Files to skip: {len(ls_files_to_skip)}")
        print_logger(f"Size of files to skip: {size_of_skip / 1e9:.2f} GB")


# %%
# Main #


if __name__ == "__main__":
    dry_run = False
    start_time = time.time()
    ls_tasks = get_list_download_tasks()
    remove_unwanted_files(ls_tasks, dry_run=dry_run)
    download_files(ls_tasks, dry_run=dry_run)
    end_time = time.time()

    print_logger(f"Time taken: {end_time - start_time} seconds", as_break=True)


# %%
