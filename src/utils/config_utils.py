# %%
# Paths #

import os
import sys

file_dir = os.path.dirname(os.path.realpath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
grandparent_dir = os.path.dirname(parent_dir)
great_grandparent_dir = os.path.dirname(grandparent_dir)

data_dir = os.path.join(grandparent_dir, "data")
trigger_dir = os.path.join(grandparent_dir, "triggers")
log_dir = os.path.join(grandparent_dir, "logs")
query_dir = os.path.join(grandparent_dir, "queries")
data_dir_db_mirror = os.path.join(
    great_grandparent_dir, "Labor_Planning", "data_db_mirror"
)
drive_download_cache_dir = os.path.join(data_dir, "drive_download_cache")
temp_upload_dir = os.path.join(data_dir, "temp_upload")

src_dir = os.path.join(grandparent_dir, "src")

directories = [
    data_dir,
    trigger_dir,
    log_dir,
    drive_download_cache_dir,
    temp_upload_dir,
]
for directory in directories:
    if not os.path.exists(directory):
        print(f"Creating directory: {directory}")
        os.makedirs(directory)

sys.path.append(src_dir)

if __name__ == "__main__":
    print(f"file_dir: {file_dir}")
    print(f"parent_dir: {parent_dir}")
    print(f"grandparent_dir: {grandparent_dir}")
    print(f"data_dir: {data_dir}")


# %%
