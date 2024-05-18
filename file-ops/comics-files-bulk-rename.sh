#!/bin/bash

# Renames comic files downloaded from a specifc source that use a specific format.
# Removes extra data from file name; for example: 
# Batman - The Brave and the Bold 012 (2024) (Webrip) (The Last Kryptonian-DCP).cbr becomes
# Batman - The Brave and the Bold 012.cbr
# Should be used only on backups of comics you have purchased

# Loop through all files in the current directory
for filename in *; do
  # Check if the filename contains a dot (likely indicating an extension)
  if [[ $filename =~ \. ]]; then
    # Extract the filename without the extension
    base_name="${filename%.*}"
    # Extract the extension
    extension="${filename##*.}"
    
    # Remove everything from the first bracket ([) to the end
    new_name="${base_name%%(*)}"
    
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
