# %%
# Imports #

import os
import sys

file_dir = os.path.dirname(os.path.realpath(__file__))
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
grandparent_dir = os.path.dirname(parent_dir)
great_grandparent_dir = os.path.dirname(grandparent_dir)

data_dir = os.path.join(parent_dir, "data")
src_dir = os.path.join(parent_dir, "src")
src_utils_dir = os.path.join(src_dir, "utils")

sys.path.append(file_dir)
sys.path.append(parent_dir)
sys.path.append(grandparent_dir)
sys.path.append(src_dir)
sys.path.append(src_utils_dir)

if __name__ == "__main__":
    print(f"file_dir: {file_dir}")
    print(f"parent_dir: {parent_dir}")
    print(f"grandparent_dir: {grandparent_dir}")


# %%
