# %%
# Imports #

import argparse
import os
import platform
import subprocess
import sys
import time

import pandas as pd
import yaml
from dotenv import load_dotenv

from config import parent_dir  # noqa: F401
from plex_api_wrapper import (
    get_dict_plex_movie_data,
    get_dict_plex_show_data,
    get_episode_data_for_season_key,
    get_seasons_data_for_show_id,
)
from utils.display_tools import (  # noqa: F401
    pprint_df,
    pprint_dict,
    pprint_ls,
    print_logger,
)

# %%
# Variables #


OPERATING_SYSTEM = "Windows" if os.name == "nt" else "Linux"
DOTENV_PATH = os.path.join(parent_dir, ".env")
if os.path.exists(DOTENV_PATH):
    load_dotenv(DOTENV_PATH)

CONIFG_FILE_CHECK_PATHS = [
    os.path.join(parent_dir, "config.yaml"),
    os.path.join(os.path.expanduser("~"), "sync_plex", "config.yaml"),
]


# %%
# Configuration #


def init_config(file_path):
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
        "The Good Place": 5,
        "The Last of Us": 5,
        "The Legend of Vox Machina": 5,
        "Arcane": 5,
        "Breaking Bad": 5,
        "The Witcher": 5,
        "The Apothecary Diaries": 15,
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
        "Dungeaons & Dragons: Honor Among Thieves",
    ]

    ls_quality_profile_pref = [
        "original",
        "optimized for mobile",
    ]

    def get_source_root_path():
        if OPERATING_SYSTEM == "Windows":
            return "\\\\192.168.86.31\\Media"
        elif OPERATING_SYSTEM == "Linux":
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

    def write_media_config(
        dict_shows: dict[str, int],
        list_movies: list[str],
        library_src_path: str,
        destination_root_path: str,
        out_path: str = "config.yaml",
    ) -> None:
        shows = [
            {"name": name, "num_next_episodes": num_next_episodes}
            for name, num_next_episodes in dict_shows.items()
        ]
        movies = [{"name": name} for name in list_movies]
        quality_profile_pres = [
            {"quality_profile": name} for name in ls_quality_profile_pref
        ]
        data = {
            "library_src_path": library_src_path,
            "destination_root_path": destination_root_path,
            "shows": shows,
            "movies": movies,
            "quality_profile_pref": quality_profile_pres,
        }

        with open(out_path, "w") as f:
            yaml.dump(data, f, sort_keys=False)

    print_logger(
        "Config file not found. Creating a new one.",
        level="warning",
    )
    # write yml
    write_media_config(
        dict_shows=dict_shows_to_watch,
        list_movies=ls_movies_to_watch,
        library_src_path=get_source_root_path(),
        destination_root_path=get_destination_root_path(),
        out_path=file_path,
    )


def get_dict_config():
    for possible_config_path in CONIFG_FILE_CHECK_PATHS:
        if not os.path.exists(possible_config_path):
            continue

        with open(possible_config_path, "r") as f:
            config = yaml.safe_load(f)
        shows = {item["name"]: item["num_next_episodes"] for item in config["shows"]}
        movies = [item["name"] for item in config["movies"]]
        if "quality_profile_pref" not in config:
            config["quality_profile_pref"] = [
                {"quality_profile": "original"},
                {"quality_profile": "optimized for mobile"},
            ]
        ls_quality_profile_pref = [
            item["quality_profile"] for item in config["quality_profile_pref"]
        ]
        return (
            config["library_src_path"],
            config["destination_root_path"],
            ls_quality_profile_pref,
            shows,
            movies,
        )

    # init at first path if no config exists
    init_config(CONIFG_FILE_CHECK_PATHS[0])
    return get_dict_config()


# %%
# Path Converter #


def get_server_mapped_path(server_relative_path):
    video_norm_path = os.path.normpath(server_relative_path)
    video_mapped_path = video_norm_path.replace("\\data", library_src_path)

    return video_mapped_path


def get_dest_path_for_source_path(source_file_path):
    dest_path = source_file_path.replace(library_src_path, destination_root_path)
    # split to list
    ls_dest_path_split = dest_path.split("\\")
    # loop through list, remove item if starts with "Season "
    for i, item in enumerate(ls_dest_path_split):
        if item.startswith("Season "):
            ls_dest_path_split.pop(i)
            break

    # if on windows add backslack after drive letter
    if OPERATING_SYSTEM == "Windows":
        ls_dest_path_split[0] = ls_dest_path_split[0] + "\\"

    # join list back to string
    dest_path = os.path.join(*ls_dest_path_split)
    return dest_path


def get_best_fit_media_item(ls_media_items, ls_quality_profile_pref, force_first=False):
    for quality_profile in ls_quality_profile_pref:
        for media_item in ls_media_items:
            for part in media_item["Part"]:
                quality_this_part = part.get("title", "")
                if (
                    quality_this_part.lower() != quality_profile.lower()
                    and not force_first
                ):
                    continue
                video_mapped_path = get_server_mapped_path(part.get("file"))
                file_size = part.get("size", 0)
                file_size_gb = file_size / 1e9

                return video_mapped_path, file_size_gb, quality_this_part

    return get_best_fit_media_item(
        ls_media_items, ls_quality_profile_pref, force_first=True
    )


# %%


def get_dict_plex_desired_movie_data(ls_movies_to_watch, ls_quality_profile_pref):
    movies_data = get_dict_plex_movie_data()
    ls_dicts_desired_movies = []

    for movie in movies_data["MediaContainer"]["Metadata"]:
        movie_title = movie["title"]

        # check if movie is desired
        for watch_movie in ls_movies_to_watch:
            if watch_movie == movie_title.split(" (")[0]:
                break
        else:
            continue

        dict_this_movie = {}
        dict_this_movie["media_type"] = "movie"

        dict_this_movie["title"] = movie_title
        dict_this_movie["view_count"] = movie.get("viewCount", 0)

        (
            dict_this_movie["server_path"],
            dict_this_movie["server_file_size_gb"],
            dict_this_movie["quality_this_part"],
        ) = get_best_fit_media_item(movie["Media"], ls_quality_profile_pref)

        dict_this_movie["dest_path"] = get_dest_path_for_source_path(
            dict_this_movie["server_path"]
        )

        ls_dicts_desired_movies.append(dict_this_movie)

    return ls_dicts_desired_movies


def get_dict_plex_desired_show_data(dict_shows_to_watch, ls_quality_profile_pref):
    shows_data = get_dict_plex_show_data()

    ls_dict_desired_shows = []

    for show in shows_data["MediaContainer"]["Metadata"]:
        show_title = show["title"]
        # check if show is desired
        if show_title not in dict_shows_to_watch.keys():
            continue

        num_episodes_of_show = dict_shows_to_watch[show_title]
        num_episodes_added = 0
        flag_have_enough_of_show = False

        show_id = show["ratingKey"]

        # get children from api
        all_seasons_data = get_seasons_data_for_show_id(show_id)

        for season_data in all_seasons_data:
            if flag_have_enough_of_show:
                break
            seasons_key = season_data["ratingKey"]

            # get episodes from api
            all_episodes_data_this_season = get_episode_data_for_season_key(seasons_key)

            for episode_data in all_episodes_data_this_season:
                episode_title = episode_data["title"]
                view_count = episode_data.get("viewCount", 0)
                # if watched, then skip
                if view_count > 0:
                    continue

                dict_this_episode = {}

                dict_this_episode["media_type"] = "show"

                (
                    dict_this_episode["server_path"],
                    dict_this_episode["server_file_size_gb"],
                    dict_this_episode["quality_this_part"],
                ) = get_best_fit_media_item(
                    episode_data["Media"], ls_quality_profile_pref
                )

                dict_this_episode["title"] = show_title
                dict_this_episode["season"] = season_data["title"]
                dict_this_episode["episode_number"] = episode_data["index"]
                dict_this_episode["episode_title"] = episode_title
                dict_this_episode["view_count"] = view_count
                dict_this_episode["dest_path"] = get_dest_path_for_source_path(
                    dict_this_episode["server_path"]
                )

                if num_episodes_added == num_episodes_of_show:
                    flag_have_enough_of_show = True
                    break
                num_episodes_added += 1

                ls_dict_desired_shows.append(dict_this_episode)

    return ls_dict_desired_shows


def get_list_dicts_desired_files(
    ls_movies_to_watch, dict_shows_to_watch, ls_quality_profile_pref
):
    ls_dicts_desired_movies = get_dict_plex_desired_movie_data(
        ls_movies_to_watch, ls_quality_profile_pref
    )
    ls_dicts_desired_shows = get_dict_plex_desired_show_data(
        dict_shows_to_watch, ls_quality_profile_pref
    )

    # combine lists
    ls_dicts_desired_files = ls_dicts_desired_movies + ls_dicts_desired_shows

    return ls_dicts_desired_files


def get_ls_dicts_existing_files(destination_root_path):
    ls_dicts_existing_files = []
    size_of_existing_files = 0
    for clean_dir in ["TV", "Movies"]:
        for root, dirs, files in os.walk(
            os.path.join(destination_root_path, clean_dir)
        ):
            for file in files:
                file_path = os.path.join(root, file)
                file_size = os.path.getsize(file_path)
                size_of_existing_files += file_size
                ls_dicts_existing_files.append(
                    {
                        "dest_path": file_path,
                        "dest_file_size_gb": file_size / 1e9,
                    }
                )

    return ls_dicts_existing_files, size_of_existing_files


# %%
# Imports #


def print_status(df_actions, current_task=""):
    os.system("cls" if OPERATING_SYSTEM == "Windows" else "clear")
    print("=" * 150)
    print(f"Status: {current_task}")
    print("=" * 150)
    pprint_df(
        df_actions[
            [
                "status",
                "media_type",
                "title",
                "season",
                "episode_number",
                "episode_title",  # reenable
                "sync_state",
                "size_diff_gb",
                "server_file_size_gb",
                "dest_file_size_gb",
                # "server_path",
                "dest_path",
                # "view_count",
                # "quality_this_part",
            ]
        ]
    )

    # print sum of filed to delete, sum of filed to download sizes
    size_of_filed_deleted = df_actions[
        (df_actions["sync_state"] == "should delete")
        & (df_actions["status"] == "deleted")
    ]["dest_file_size_gb"].sum()
    size_of_files_total_to_delete = df_actions[
        df_actions["sync_state"] == "should delete"
    ]["dest_file_size_gb"].sum()

    num_filed_deleted = df_actions[
        (df_actions["sync_state"] == "should delete")
        & (df_actions["status"] == "deleted")
    ].shape[0]
    num_files_total_to_delete = df_actions[
        df_actions["sync_state"] == "should delete"
    ].shape[0]

    size_of_filed_downloaded = df_actions[
        (df_actions["sync_state"] == "need download")
        & (df_actions["status"] == "downloaded")
    ]["server_file_size_gb"].sum()
    size_of_files_total_to_download = df_actions[
        df_actions["sync_state"] == "need download"
    ]["server_file_size_gb"].sum()

    numn_files_downloaded = df_actions[
        (df_actions["sync_state"] == "need download")
        & (df_actions["status"] == "downloaded")
    ].shape[0]
    num_files_total_to_download = df_actions[
        df_actions["sync_state"] == "need download"
    ].shape[0]

    # print a tool bar at the bottom showing status of all
    print("=" * 150)
    print(
        f"File deletion: {num_filed_deleted}/{num_files_total_to_delete}, size: {size_of_filed_deleted:.2f} GB/{size_of_files_total_to_delete:.2f} GB ----- File downloads: {numn_files_downloaded}/{num_files_total_to_download}, size: {size_of_filed_downloaded:.2f} GB/{size_of_files_total_to_download:.2f} GB"
    )
    print("=" * 150)


def copy_file(src_path, dest_path):
    if not os.path.exists(dest_path):
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    if OPERATING_SYSTEM == "Windows":
        result = subprocess.run(
            [
                "robocopy",
                os.path.dirname(src_path),
                os.path.dirname(dest_path),
                os.path.basename(src_path),
            ],
            capture_output=True,  # Capture the output of the command
            text=True,  # Return the output as a string (Python 3.7+)
        )
        success = result.returncode == 1
        if not success:
            print_logger(
                f"Error copying file {src_path} to {dest_path}, error: {result}",
                level="error",
            )
    elif OPERATING_SYSTEM == "Linux":
        print_logger(f"Using rsync to copy {src_path} to {dest_path}")
        subprocess.run(["rsync", "-av", src_path, dest_path])
    else:
        raise Exception("Operating system not recognized")


def apply_sync(df_actions):
    print_status(df_actions, "Beginning sync")
    for index, row in df_actions.iterrows():
        if row["sync_state"] == "should delete":
            df_actions.at[index, "status"] = "deleting"
            print_status(df_actions, "Deleting")

            os.remove(row["dest_path"])

            df_actions.at[index, "status"] = "deleted"
            print_status(df_actions, "Done deleting")

    for index, row in df_actions.iterrows():
        if row["sync_state"] == "need download":
            df_actions.at[index, "status"] = "downloading"
            print_status(df_actions, "Downloading")

            copy_file(row["server_path"], row["dest_path"])

            df_actions.at[index, "status"] = "downloaded"
            print_status(df_actions, "Done downloading")


# %%
# Main #


if __name__ == "__main__":
    start_time = time.time()
    destination_root_path = ""
    if "ipykernel" in sys.argv[0]:
        print("Running in IPython kernel")
    else:
        parser = argparse.ArgumentParser(description="Sync media to folder.")
        parser.add_argument(
            "path", type=str, help="Folder path to pull config and sync"
        )

        args = parser.parse_args()

        if args.path:
            destination_root_path = os.path.abspath(args.path)

    print("Path to sync:", destination_root_path)

    (
        library_src_path,
        _,
        ls_quality_profile_pref,
        dict_shows_to_watch,
        ls_movies_to_watch,
    ) = get_dict_config()

    ls_dicts_desired_files = get_list_dicts_desired_files(
        ls_movies_to_watch,
        dict_shows_to_watch,
        ls_quality_profile_pref,
    )

    ls_dicts_existing_files, size_of_existing_files = get_ls_dicts_existing_files(
        destination_root_path
    )

    # convert both to dataframes and merge on dest path
    df_desired_files = pd.DataFrame(ls_dicts_desired_files)
    df_existing_files = pd.DataFrame(ls_dicts_existing_files)

    df_merged = pd.merge(
        df_desired_files,
        df_existing_files,
        on="dest_path",
        how="outer",
        indicator="sync_state",
    )
    # change indicator to string
    df_merged["sync_state"] = df_merged["sync_state"].astype(str)

    # replace indicator with state
    df_merged["sync_state"] = df_merged["sync_state"].replace(
        {"both": "synced", "left_only": "need download", "right_only": "should delete"}
    )

    # size diff
    df_merged["size_diff_gb"] = df_merged["server_file_size_gb"].astype(
        float
    ) - df_merged["dest_file_size_gb"].astype(float)

    df_merged = df_merged[
        [
            "media_type",
            "title",
            "season",
            "episode_number",
            "episode_title",
            "sync_state",
            "size_diff_gb",
            "server_file_size_gb",
            "dest_file_size_gb",
            "server_path",
            "dest_path",
            "view_count",
            "quality_this_part",
        ]
    ]

    for column_name in ["season", "episode_number", "episode_title"]:
        df_merged[column_name] = df_merged[column_name].fillna("")
        df_merged[column_name] = (
            df_merged[column_name]
            .astype(str)
            .str.replace(".00", "")
            .str.replace(".0", "")
        )

    df_actions = df_merged.copy()
    df_actions.loc[df_actions["sync_state"] == "synced", "status"] = "synced"
    # filter to non synced
    df_actions = df_actions[df_actions["status"] != "synced"]

    df_actions["status"] = "pending"
    print_status(df_actions, "User Confirmation")

    user_string = input(
        "Type 'sync' to start the download process, or 'exit' to exit: "
    )
    if user_string.lower() == "exit":
        print_logger("Exiting...")
        exit()
    elif user_string.lower() != "sync":
        print_logger(
            "Invalid input. Exiting...",
            level="error",
        )
        exit()
    else:
        print_logger("Starting sync process...")

    apply_sync(df_actions)

    print_logger(f"Time taken: {time.time() - start_time:.2f} seconds")


# %%
