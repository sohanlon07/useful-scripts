#!/bin/bash

# Renames comic files downloaded from a specifc source that use a specific format.
# Removes extra data from file name; for example: 
# Deadpool-Wolverine 004 (2025) (Digital) (Kileko-Empire).cbz becomes
# Deadpool-Wolverine 004 (2025).cbz
# Should be used only on backups of comics you have purchased

# Loop through all .cbr and .cbz files in the current directory
for filename in *.cbr *.cbz; do
  # Check if the filename contains a dot (likely indicating an extension)
  if [[ $filename =~ \. ]]; then
    # Extract the filename without the extension
    base_name="${filename%.*}"
    # Extract the extension
    extension="${filename##*.}"
    
    # If the filename has a year in parenthesis, like (2024), keep it and remove trailing info
    if [[ "$base_name" =~ (.*[[:space:]]\([0-9]{4}\)) ]]; then
      new_name="${BASH_REMATCH[1]}"
    else
      # Otherwise, remove everything from the first parenthesis
      new_name="${base_name%% (*}"
    fi
    
    # Remove trailing spaces from the new name
    new_name="${new_name%% }"  # Double expansion for removing trailing spaces
    
    # Rebuild the filename with the new name and extension, removing space
    new_filename="$new_name.$extension"
    
    # Rename the file only if the new name is different
    if [[ "$filename" != "$new_filename" ]]; then
      mv "$filename" "$new_filename"
      echo "Renamed: $filename -> $new_filename"
    fi
  fi
done

echo "Finished renaming files."
