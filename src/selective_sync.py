# %%
# Imports #

if __name__ != "__main__":
    print(f"Importing {__name__}")

import json
import os
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from dotenv import load_dotenv

# append grandparent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import utils.android_tools
from config import (
    data_dir,
    file_dir,
    grandparent_dir,
    great_grandparent_dir,
    parent_dir,
)
from utils.display_tools import pprint_df, pprint_dict, pprint_ls, print_logger

# %%
# Variables #


host_operating_system = "Windows" if os.name == "nt" else "Linux"

config_file_path = os.path.join(parent_dir, "sync_config.json")

default_config = {
    "sync_folders": [
        {
            "sync_name": "Audiobooks",
            "src_path_type": "Windows Network Mount",
            "src_path": ["192.168.86.31", "Media", "Audiobooks"],
            "dest_path_type": "Windows Local Drive",
            "dest_path": ["I:", "Media", "Audiobooks"],
            "included_subfolders": [
                ["Orson Scott Card", "Enderverse- Publication Order"],
                ["Orson Scott Card", "The Shadow Series"],
                [
                    "Orson Scott Card",
                    "Ender's Saga",
                    "Ender's Saga 0.5 - First Meetings- In the Enderverse",
                ],
                [
                    "Orson Scott Card",
                    "Ender's Saga",
                    "Ender's Saga 4 - Children of the Mind",
                ],
                [
                    "Orson Scott Card",
                    "Ender's Saga",
                    "Ender's Saga 5 - Ender in Exile",
                ],
                [
                    "Sarah J. Maas",
                    "Throne of Glass",
                ],
            ],
            "included_files": [],
        }
    ]
}

# if it doesnt exist, creat it with comments on formatting
if not os.path.isfile(config_file_path) or True:
    with open(config_file_path, "w") as f:
        json.dump(default_config, f, indent=4)

with open(config_file_path, "r") as f:
    config = json.load(f)

print(config)


# %%
# Functions #


def clean_destination_directory(dest_path, included_subfolders, included_files):
    """
    Removes files and directories from the destination directory that are not part of the sync configuration.
    """
    # Handle None for included_subfolders and included_files
    if included_subfolders is None:
        included_subfolders = []
    if included_files is None:
        included_files = []

    # Convert included subfolders and files to absolute paths for easier comparison
    included_subfolder_paths = {
        os.path.join(dest_path, *subfolder) for subfolder in included_subfolders
    }
    included_file_paths = {os.path.join(dest_path, *file) for file in included_files}

    # Walk through the destination directory
    for root, dirs, files in os.walk(dest_path, topdown=False):
        for file in files:
            file_path = os.path.join(root, file)
            # Check if the file is in an included subfolder or is an included file
            if (
                not any(
                    root.startswith(folder_path)
                    for folder_path in included_subfolder_paths
                )
                and file_path not in included_file_paths
            ):
                print_logger(f"Removing file: {file_path}")
                os.remove(file_path)

        # If the directory is not in the list of included subfolders, check if it is empty before removing
        if not any(
            root.startswith(folder_path) for folder_path in included_subfolder_paths
        ):
            # Check if the directory is empty
            if not os.listdir(root):
                print_logger(f"Removing empty directory: {root}")
                shutil.rmtree(root)


def sync_directory(
    src_path_type,
    src_path,
    dest_path_type,
    dest_path,
    included_subfolders=None,
    included_files=None,
):
    """
    Syncs a directory from a source to a destination
    """

    if src_path_type == "Windows Network Mount":
        path_prefix = "\\\\"
        src_path = path_prefix + "\\".join(src_path)

    if dest_path_type == "Windows Local Drive":
        path_prefix = ""
        dest_path = path_prefix + "\\".join(dest_path)

    if included_subfolders is not None:
        for subfolder in included_subfolders:
            print_logger(f"Syncing subfolder: {subfolder}")
            src_subfolder = os.path.join(src_path, *subfolder)
            dest_subfolder = os.path.join(dest_path, *subfolder)
            print_logger(f"Syncing: {src_subfolder} to {dest_subfolder}")
            # if windows then robocopy
            if host_operating_system == "Windows":
                subprocess.run(
                    [
                        "robocopy",
                        src_subfolder,
                        dest_subfolder,
                        "/MIR",  # enable to mirror the source directory
                        "/Z",  # enable to restartable mode
                        "/R:5",  # number of retries on failed copies
                        "/W:5",  # wait time between retries
                    ]
                )

    if included_files is not None:
        for file in included_files:
            print_logger(f"Syncing file: {file}")
            src_file = os.path.join(src_path, *file)
            dest_file = os.path.join(dest_path, *file)
            print_logger(f"Syncing: {src_file} to {dest_file}")
            # if windows then robocopy for a single file
            if host_operating_system == "Windows":
                subprocess.run(
                    [
                        "robocopy",
                        os.path.dirname(src_file),
                        os.path.dirname(dest_file),
                        os.path.basename(dest_file),
                    ]
                )

    clean_destination_directory(dest_path, included_subfolders, included_files)


for sync_folder in config["sync_folders"]:
    sync_directory(
        src_path_type=sync_folder["src_path_type"],
        src_path=sync_folder["src_path"],
        dest_path_type=sync_folder["dest_path_type"],
        dest_path=sync_folder["dest_path"],
        included_subfolders=sync_folder["included_subfolders"],
        included_files=sync_folder["included_files"],
    )


# %%
