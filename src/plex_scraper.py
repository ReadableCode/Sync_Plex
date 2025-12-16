# %%
# Imports #

import argparse
import os
import platform
import shutil
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

OPERATING_SYSTEM = platform.system()

if OPERATING_SYSTEM == "Windows":
    print("Running on Windows")
elif OPERATING_SYSTEM == "Linux":
    print("Running on Linux")
elif OPERATING_SYSTEM == "Darwin":
    print("Running on macOS")
else:
    print(f"Running on an unknown system: {OPERATING_SYSTEM}")

DOTENV_PATH = os.path.join(parent_dir, ".env")
if os.path.exists(DOTENV_PATH):
    load_dotenv(DOTENV_PATH)

# %%
# Configuration #


def get_source_root_path():
    if OPERATING_SYSTEM == "Windows":
        return "\\\\192.168.86.31\\Media"
    elif OPERATING_SYSTEM == "Linux":
        return "/mnt/192.168.86.31/Media"  # TODO fix this on a system with a mount
    elif OPERATING_SYSTEM == "Darwin":
        return "/Volumes/Media"
    else:
        raise Exception("Operating system not recognized")


def init_config(file_path):
    dict_shows_to_watch = {
        "American Dad!": 3,
    }

    ls_movies_to_watch = [
        "Zootopia",
    ]

    ls_quality_profile_pref = [
        "original",
        "optimized for mobile",
    ]

    def write_media_config(
        dict_shows: dict[str, int],
        list_movies: list[str],
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
        out_path=file_path,
    )


def get_dict_config(destination_root_path):
    config_path = os.path.join(destination_root_path, "config.yaml")
    if not os.path.exists(config_path):
        user_input = input(
            f"Config file not found at {config_path}. Do you want to create a new one? (y/n): "
        )
        if user_input.lower() != "y":
            print_logger(
                "Exiting...",
                level="error",
            )
            exit()
        # init at first path if no config exists
        init_config(config_path)
        return get_dict_config(destination_root_path)

    print_logger(
        f"Config file found at {config_path}",
        level="info",
    )
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    # shows = {item["name"]: item["num_next_episodes"] for item in config["shows"]}
    shows = {}
    for item in config["shows"]:
        shows[item["name"]] = {
            "num_next_episodes": item["num_next_episodes"],
            "only_get_unwatched": item.get("only_get_unwatched", True),
        }

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
        ls_quality_profile_pref,
        shows,
        movies,
    )


# %%
# Path Converter #


def get_server_mapped_path(server_relative_path):
    video_norm_path = os.path.normpath(server_relative_path)
    video_mapped_path = video_norm_path.replace(
        "\\data", get_source_root_path()
    ).replace("/data", get_source_root_path())

    return video_mapped_path


def get_dest_path_for_source_path(source_file_path, destination_root_path):
    dest_path = source_file_path.replace(get_source_root_path(), destination_root_path)
    # split to list
    if OPERATING_SYSTEM == "Windows":
        ls_dest_path_split = dest_path.split("\\")
    else:
        ls_dest_path_split = dest_path.split(os.sep)
    # loop through list, remove item if starts with "Season "
    for i, item in enumerate(ls_dest_path_split):
        if item.startswith("Season "):
            ls_dest_path_split.pop(i)
            break

    # if on windows add backslack after drive letter
    if OPERATING_SYSTEM == "Windows":
        ls_dest_path_split[0] = ls_dest_path_split[0] + "\\"

    # join list back to string
    if OPERATING_SYSTEM == "Windows":
        dest_path = os.path.join(*ls_dest_path_split)
    else:
        dest_path = os.sep + os.path.join(*ls_dest_path_split)
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


def get_dict_plex_desired_movie_data(
    ls_movies_to_watch, ls_quality_profile_pref, destination_root_path
):
    movies_data = get_dict_plex_movie_data()
    movie_meta_data = movies_data.get("MediaContainer", {}).get("Metadata", [])

    ls_dicts_desired_movies = []
    ls_movies_found = []

    for plex_movie_dict in movie_meta_data:
        # get movie title without year
        movie_title = plex_movie_dict["title"].split(" (")[0]
        # check if movie is desired
        if movie_title in ls_movies_to_watch:
            ls_movies_found.append(movie_title)

            dict_this_movie = {
                "media_type": "movie",
                "title": plex_movie_dict["title"],
                "view_count": plex_movie_dict.get("viewCount", 0),
            }

            (
                dict_this_movie["server_path"],
                dict_this_movie["server_file_size_gb"],
                dict_this_movie["quality_this_part"],
            ) = get_best_fit_media_item(
                plex_movie_dict["Media"], ls_quality_profile_pref
            )

            dict_this_movie["dest_path"] = get_dest_path_for_source_path(
                dict_this_movie["server_path"], destination_root_path
            )

            ls_dicts_desired_movies.append(dict_this_movie)

    return ls_dicts_desired_movies


def get_dict_plex_desired_show_data(
    dict_shows_to_watch, ls_quality_profile_pref, destination_root_path
):
    shows_data = get_dict_plex_show_data()
    found_titles = set()
    ls_dict_desired_shows = []

    for show in shows_data["MediaContainer"]["Metadata"]:
        show_title = show["title"]
        # check if show is desired
        if show_title not in dict_shows_to_watch:
            continue

        found_titles.add(show_title)
        num_episodes_of_show = dict_shows_to_watch[show_title]["num_next_episodes"]
        only_get_unwatched = dict_shows_to_watch[show_title].get(
            "only_get_unwatched", True
        )
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
                if episode_data.get("viewCount", 0) > 0 and only_get_unwatched:
                    continue

                dict_this_episode = {
                    "media_type": "show",
                    "title": show_title,
                    "season": season_data["title"],
                    "episode_number": episode_data["index"],
                    "episode_title": episode_data["title"],
                    "view_count": episode_data.get("viewCount", 0),
                }

                (
                    dict_this_episode["server_path"],
                    dict_this_episode["server_file_size_gb"],
                    dict_this_episode["quality_this_part"],
                ) = get_best_fit_media_item(
                    episode_data["Media"], ls_quality_profile_pref
                )

                dict_this_episode["dest_path"] = get_dest_path_for_source_path(
                    dict_this_episode["server_path"], destination_root_path
                )

                ls_dict_desired_shows.append(dict_this_episode)

                num_episodes_added += 1
                if num_episodes_added == num_episodes_of_show:
                    flag_have_enough_of_show = True
                    break

    missing_titles = set(dict_shows_to_watch) - found_titles
    if missing_titles:
        raise ValueError(
            f"Desired shows not found in Plex: {', '.join(sorted(missing_titles))}"
        )

    return ls_dict_desired_shows


def get_list_dicts_desired_files(destination_root_path):
    (
        ls_quality_profile_pref,
        dict_shows_to_watch,
        ls_movies_to_watch,
    ) = get_dict_config(destination_root_path)

    ls_dicts_desired_movies = get_dict_plex_desired_movie_data(
        ls_movies_to_watch, ls_quality_profile_pref, destination_root_path
    )
    ls_dicts_desired_shows = get_dict_plex_desired_show_data(
        dict_shows_to_watch, ls_quality_profile_pref, destination_root_path
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
                if file.startswith("._"):
                    continue
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
    df_actions = df_actions.copy()

    # make dest path shorter
    df_actions["dest_path"] = df_actions["dest_path"].apply(
        lambda x: os.path.basename(x)
    )

    os.system("cls" if OPERATING_SYSTEM == "Windows" else "clear")
    print("=" * 150)
    print(f"Status: {current_task}")
    print("=" * 150)
    df_actions["size"] = df_actions["server_file_size_gb"].fillna(df_actions["dest_file_size_gb"])
    pprint_df(
        df_actions[
            [
                "status",
                "media_type",
                "title",
                "season",
                "episode_number",
                # "episode_title",
                "sync_state",
                # "size_diff_gb",
                # "server_file_size_gb",
                # "dest_file_size_gb",
                "size",
                # "server_path",
                "dest_path",
                # "view_count",
                # "quality_this_part",
            ]
        ]
    )

    # print sum of files to delete, sum of files to download sizes
    size_of_files_deleted = df_actions[
        (df_actions["sync_state"] == "should delete")
        & (df_actions["status"] == "deleted")
    ]["dest_file_size_gb"].sum()
    size_of_files_total_to_delete = df_actions[
        df_actions["sync_state"] == "should delete"
    ]["dest_file_size_gb"].sum()

    num_files_deleted = df_actions[
        (df_actions["sync_state"] == "should delete")
        & (df_actions["status"] == "deleted")
    ].shape[0]
    num_files_total_to_delete = df_actions[
        df_actions["sync_state"] == "should delete"
    ].shape[0]

    size_of_files_downloaded = df_actions[
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
        f"File deletion: {num_files_deleted}/{num_files_total_to_delete}, "
        f"size: {size_of_files_deleted:.2f} GB/{size_of_files_total_to_delete:.2f} GB "
        "----- "
        f"File downloads: {numn_files_downloaded}/{num_files_total_to_download}, "
        f"size: {size_of_files_downloaded:.2f} GB/{size_of_files_total_to_download:.2f} GB"
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
    elif OPERATING_SYSTEM == "Darwin":
        print_logger(f"Using shutil to copy {src_path} to {dest_path}")
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        shutil.copy(src_path, dest_path)
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
    destination_root_path = "/Users/jason/Media/"
    if "ipykernel" in sys.argv[0]:
        print("Running in IPython kernel")
    else:
        parser = argparse.ArgumentParser(description="Sync media to folder.")
        parser.add_argument(
            "path",
            type=str,
            nargs="?",
            default=destination_root_path,
            help="Folder path to pull config and sync",
        )

        args = parser.parse_args()

        if args.path:
            destination_root_path = os.path.abspath(args.path)

    print_logger(f"Path to sync: {destination_root_path}")

    print_logger(
        f"Destination root path: {destination_root_path}",
    )

    ls_dicts_desired_files = get_list_dicts_desired_files(destination_root_path)

    ls_dicts_existing_files, size_of_existing_files = get_ls_dicts_existing_files(
        destination_root_path
    )
    # if no existing file, still create correct columns
    if not ls_dicts_existing_files:
        df_existing_files = pd.DataFrame(columns=["dest_path", "dest_file_size_gb"])
    else:
        df_existing_files = pd.DataFrame(ls_dicts_existing_files)

    # convert both to dataframes and merge on dest path
    df_desired_files = pd.DataFrame(ls_dicts_desired_files)

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

    # print entire dataframe as list of dictionaries
    dict_df = df_merged.to_dict(orient="records")
    print_logger("Merged DataFrame as list of dictionaries:")
    pprint_dict(dict_df)

    # are you sure again
    if input("Do you want to continue with these files? (y/n): ").lower() != "y":
        print_logger(
            "Exiting...",
        )
        exit()

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
