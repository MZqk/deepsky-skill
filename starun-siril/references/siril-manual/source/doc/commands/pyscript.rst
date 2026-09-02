| Executes a Siril python script
| 
| The script name must be provided as the first argument. If it is not found in the current working directory, the user-defined script paths specified in Preferences and the local siril-scripts repository will be searched. All subsequent arguments will be treated as script arguments and passed to the script as its argument vector. Note that the specific script must incorporate support for reading input from the argument vector
