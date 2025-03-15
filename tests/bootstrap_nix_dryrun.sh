# if git doesnt exist
if [ -z "$(command -v "git")" ]
then
    echo "not even sure how you got this code without git, please install git"
    exit
fi

# if python3 doesnt exist
if [ -z "$(command -v "python3")" ]
then
    echo "please install python3"
    exit
fi

# if nix doesnt exist
if [ -z "$(command -v "nix")" ]
then
    echo "please install nix"
    exit
fi

# if nix-prefetch doesnt exist
if [ -z "$(command -v "nix-prefetch")" ]
then
    nix-env -iA nixpkgs.nix-prefetch -I nixpkgs=https://github.com/NixOS/nixpkgs/archive/aa0e8072a57e879073cee969a780e586dbe57997.tar.gz
fi


# 
# actual setup
# 
# if superflore-gen-nix doesnt exist
if [ -z "$(command -v "superflore-gen-nix")" ]
then
    sudo python3 ./setup.py install
    sudo rosdep init
    rosdep update
fi

# 
# actual run
# 
sudo python3 ./setup.py install

tar_cache_folder=".temp.ignore/tar_cache/"
mkdir -p "$tar_cache_folder"

output_folder=".temp.ignore/nix-ros-overlay/"

if ! [ -d "$output_folder" ]
then
    cd "$(dirname "$output_folder")"
    git clone git@github.com:jeff-hykin/nix-ros-overlay.git
    cd -
fi

superflore-gen-nix \
    --dry-run \
    --all \
    --output-repository-path "$output_folder" \
    --tar-archive-dir "$tar_cache_folder"