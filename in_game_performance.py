def prior_player_at_bats(data, player_id, current_at_bat_index=None):
    """
    Finds all prior at-bat indexes for a specific player up to a given current at-bat index.

    Parameters:
    - data: dict
        The game data containing live plays, including matchups and results.
    - player_id: int
        The unique identifier of the player (batter).
    - current_at_bat_index: int, optional
        The index of the current at-bat. The function will return all prior at-bat indexes up to this point.
        If not provided, the function will return all at-bat indexes for the player.

    Returns:
    - list of int
        A list of at-bat indexes for the specified player up to the given current at-bat index.
    """
    
    at_bat_indexes = []  #
    all_plays = data['liveData']['plays']['allPlays'] 
    
    for play in all_plays:
        at_bat_index = play['atBatIndex']  # Current at-bat index 
        
        # Stop searching if we've reached or passed the current at-bat index (if provided)
        if current_at_bat_index is not None and at_bat_index >= current_at_bat_index:
            break
        
        # Check if the batter in this play matches the given player ID
        batter = play.get('matchup', {}).get('batter', {})
        if batter and batter.get('id') == player_id:
            at_bat_indexes.append(at_bat_index)  # Add the at-bat index to the list
    
    return at_bat_indexes


def in_game_batting_stat_line(data, at_bat_indexes):
    """
    Summarizes a player's in-game batting performance based on prior at-bat indexes.

    Parameters:
    - data: dict
        The game data containing live plays, including matchups and results.
    - at_bat_indexes: list of int
        A list of at-bat indexes for which the player's performance should be summarized.

    Returns:
    - str
        A string summarizing the player's performance in the format: "hits-at_bats, walks BB, RBIs RBI, home_runs HR (if applicable), sac_flies SF (if applicable)".
    """
    
    all_plays = data['liveData']['plays']['allPlays']
    
    # Variables to summarize the performance
    hits = 0
    at_bats = 0
    walks = 0
    rbis = 0
    home_runs = 0
    sac_flies = 0
    
    for index in at_bat_indexes:
        if index < len(all_plays):  # Ensure the index exists
            result = all_plays[index]['result']
            
            # Summarize the performance
            event_type = result['eventType']
            is_out = result['isOut']
            rbi = result.get('rbi', 0)
            
            if event_type in ['single', 'double', 'triple', 'home_run']:  # Count hits
                if event_type == 'home_run':
                    home_runs += 1  # Count home runs separately
                else:
                    hits += 1
                at_bats += 1
            elif event_type == 'walk':  # Count walks (BB)
                walks += 1
            elif event_type == 'sac_fly':  # Count sacrifice flies
                sac_flies += 1
            elif event_type == 'strikeout' or is_out:  # Count outs as at-bats
                at_bats += 1
            
            rbis += rbi
    
    # Create the performance summary
    summary_parts = []
    summary_parts.append(f"{hits}-{at_bats}")
    
    # Add additional stats only if they are non-zero
    if walks > 0:
        summary_parts.append(f"{walks} BB")
    if rbis > 0:
        summary_parts.append(f"{rbis} RBI")
    if home_runs > 0:
        summary_parts.append(f"{home_runs} HR")
    if sac_flies > 0:
        summary_parts.append(f"{sac_flies} SF")
    
    # Join the summary parts into a single string
    stat_line_summary = ", ".join(summary_parts)
    
    return stat_line_summary

