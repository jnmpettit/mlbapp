from flask import Flask, render_template, request, redirect, url_for, send_from_directory
import os
import requests
from datetime import datetime, timedelta, time
import json  # Added for JSON handling
from flask import Flask, request, jsonify
from google.cloud import texttospeech
import base64
import vertexai
from vertexai.generative_models import GenerativeModel, SafetySetting
from vertexai.preview.vision_models import ImageGenerationModel
from google.cloud import texttospeech_v1beta1 as texttospeech
from google.api_core.client_options import ClientOptions
from PIL import Image
import io
import time
import logging
from google.api_core.exceptions import GoogleAPICallError, RetryError
from in_game_performance import prior_player_at_bats, in_game_batting_stat_line

app = Flask(__name__, template_folder="src/")

game_type_mapping = {
    "S": "Spring Training",
    "R": "Regular Season",
    "F": "Wild Card Game",
    "D": "Division Series",
    "L": "League Championship Series",
    "W": "World Series",
    "C": "Championship",
    "N": "Nineteenth Century Series",
    "P": "Playoffs",
    "A": "All-Star Game",
    "I": "Intrasquad",
    "E": "Exhibition"
}

# Load the project ID and secret name from environment variables
PROJECT_ID = os.environ.get("PROJECT_ID")
SECRET_NAME = os.environ.get("SECRET_NAME")

# Configuration
LOCATION = "us-central1"


@app.route('/favicon.ico')
def favicon():
    """
    Serve the favicon.ico file.

    Returns:
        The favicon.ico file.
    """
    return send_from_directory(os.path.join(app.root_path, 'static/images'),
                          'favicon.ico',mimetype='image/vnd.microsoft.icon')

@app.route('/')
def index():
    """
    Render the index page with a list of MLB games.

    Returns:
        The rendered index.html template.
    """
    today = datetime.today()
    start_date = today - timedelta(days=365)  # Show games from the past year
    end_date = today

    try:
        response = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start_date.strftime('%m/%d/%Y')}&endDate={end_date.strftime('%m/%d/%Y')}"
        )
        response.raise_for_status()
        games = response.json()['dates']
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"Error fetching games: {e}")
        games = []
    
    sorted_games = sorted(games, key=lambda x: x['date'],reverse=True)

    return render_template('index.html', games=sorted_games, today=today)  # Pass today's date

@app.route('/game/<int:game_pk>')
def game_details(game_pk):
    """
    Render the game details page for a specific game.

    Args:
        game_pk: The primary key of the game.

    Returns:
        The rendered game_details.html template.
    """
    try:
        response = requests.get(f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live")
        response.raise_for_status()
        game_data = response.json()
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"Error fetching game details: {e}")
        game_data = {}
    
    return render_template('game_details.html', game_data=game_data, game_type_mapping=game_type_mapping)

@app.route('/generate_game_image/<int:game_pk>')
def generate_game_image(game_pk):
    """
    Generate an image of the venue with weather conditions and time of day.

    Args:
        game_pk: The primary key of the game.

    Returns:
        A JSON response containing the base64 encoded image data.
    """
    game_data = fetch_game_data(game_pk)  # Fetch game data using the provided game_pk

    if game_data:
        # Extract nested values using dictionary keys
        venue_name = game_data['gameData']['venue']['name']
        day_night = game_data['gameData']['datetime']['dayNight']
        weather_condition = game_data['gameData']['weather']['condition']
        weather_temp = game_data['gameData']['weather']['temp']
        weather_wind = game_data['gameData']['weather']['wind']
        game_date = game_data['gameData']['datetime']['originalDate']

        # Initialize the ImageGenerationModel
        model = ImageGenerationModel.from_pretrained("imagen-3.0-generate-001")

        # Generate the image
        venue_image = model.generate_images(
            prompt=f"""Generate an image of the baseball venue with weather conditions and time of day from:
            Angle: From the announcers box
            Venue: {venue_name}
            Date: {game_date}
            Day or Night: {day_night}
            Weather: {weather_condition}, Temperature: {weather_temp}, Wind: {weather_wind}
            """,
            # Optional parameters
            number_of_images=1,
            language="en",
            aspect_ratio="1:1",
            safety_filter_level="block_some",
            person_generation="allow_adult",
        )

        # # Convert the PIL Image to base64
        timestamp = int(time.time())  # Get current timestamp
        filename = f"/tmp/{game_pk}_{timestamp}.png"
        buffered = io.BytesIO()
        venue_image[0].save(filename)
        img = Image.open(filename)
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        img.close()
        os.remove(filename)  # Remove the temporary file

        return {"imageData": img_str}  # Return the base64 encoded image data
        # # Return the generated image


@app.route('/get_games_by_date', methods=['POST'])
def get_games_by_date():
    """
    Get a list of games for a specific date.

    Returns:
        A JSON response containing the list of games.
    """

    # Get the selected date from the request data
    selected_date = request.form['date']

    try:
        # Format the date as expected by the MLB Stats API
        formatted_date = datetime.strptime(selected_date, '%Y-%m-%d').strftime('%m/%d/%Y')

        # Make the API call with the selected date
        response = requests.get(
            f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={formatted_date}"
        )
        response.raise_for_status()
        games_data = response.json()

        # Extract the list of games from the response
        games = games_data.get('dates', [{}])[0].get('games', [])  # Handle potential errors
    except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
        print(f"Error fetching games for {selected_date}: {e}")
        games = []

    # Return the list of games as JSON
    return json.dumps({'games': games})

@app.route('/generate_commentary', methods=['POST'])
def generate_commentary():
    """
    Generate game commentary using Gemini.

    Returns:
        A JSON response containing the generated commentary.
    """

    try:
        # Extract game_pk (primary key for the game) from the request
        game_pk = request.json.get("game_pk")
        style = request.json.get("style")
        language = request.json.get("language")
        if not game_pk:
            return jsonify({"error": "game_pk is required"}), 400

        # Fetch game data from the MLB API
        game_data = fetch_game_data(game_pk)
        if not game_data:
            return jsonify({"error": "Could not retrieve game data"}), 500

        # Summarize game data using Gemini
        commentary = summarize_game_data_with_gemini(game_data, style, language)

        # Return the generated commentary
        return jsonify({
            "message": "Commentary generated successfully",
            "commentary": commentary
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

MLB_API_BASE_URL = "https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
def fetch_game_data(game_pk):
    """
    Fetch live game data from the MLB Stats API.

    Args:
        game_pk: The primary key of the game.
    Returns:
        The game data as a JSON object.
    """
    url = MLB_API_BASE_URL.format(game_pk=game_pk)
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()  # Return game data as JSON
    raise Exception(f"Failed to fetch game data. Status code: {response.status_code}")

from google.cloud import aiplatform, secretmanager
from google.oauth2 import service_account

# Function to retrieve service account credentials from Secret Manager.
def get_credentials_from_secret_manager(secret_name, project_id):
    """
    Retrieves service account credentials from Secret Manager.

    Args:
        secret_name: The name of the secret in Secret Manager.
        project_id: The Google Cloud project ID.

    Returns:
        google.oauth2.service_account.Credentials: The service account credentials.
    """
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    key_content = response.payload.data.decode("UTF-8")
    return service_account.Credentials.from_service_account_info(eval(key_content))

# Summarize game data with Gemini
def summarize_game_data_with_gemini(game_data, style, language):
    """
    Summarizes game data using Vertex AI's Gemini model.

    Args:
        game_data: The game data to summarize.
        style: The desired style of the summary (e.g., "casual", "formal").
        language: The language for the summary.

    Returns:
        str: The generated game summary.
    """
    # Initialize Vertex AI
    vertexai.init(project=f"{PROJECT_ID}", location="us-central1",credentials=credentials)

    # Load the generative model
    model = GenerativeModel(
        "gemini-2.0-flash-exp",
    )

    # Define generation configuration
    generation_config = {
        "max_output_tokens": 8192,
        "temperature": 1,
        "top_p": 0.95,
    }

    # Define safety settings
    safety_settings = [
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
    ]

    # Input prompt for the model
    text1 = f"""-You are a sports commentator.
                -Summarize this baseball game. 
                - Style: {style}
                - Translate into language: {language} 
                - Only return the translated version.
                - Stich information from these questions into the summary, but keep the flow natural:
                    Who won the game?
                    What was the final score?
                    Was the game a close one or a blowout?
                    Did the game go into extra innings?
                    Who were the standout players in the game?
                    How many home runs, RBIs, or strikeouts did key players achieve?
                    Who was the winning/losing pitcher?
                    Did any player hit a milestone (e.g., 500th home run)?
                    What were the pivotal moments (e.g., walk-off hits, grand slams)?
                    Were there any controversial plays or calls?
                    Did any records get broken or set during the game?
                    How does this game affect the team’s standings?
                    What’s the team’s current win/loss streak?
                    How did the team perform compared to previous games in the series?
                    What was the total number of hits, errors, and strikeouts?
                    How did the starting pitchers perform?
                    Were there any key stats or matchups that stood out?
                    What were the most exciting or unexpected plays?
                    Were there any fan-related moments (e.g., a lucky catch)?
                    How did the crowd react to key moments?
                {game_data}
            """

    # Generate content
    responses = model.generate_content(
        [text1],
        generation_config=generation_config,
        safety_settings=safety_settings,
        stream=True,
    )

    # Collect and return the generated responses
    generated_summary = ""
    for response in responses:
        generated_summary += response.text

    return generated_summary

@app.route('/synthesize_speech', methods=['POST'])
def synthesize_speech():
    """
    Synthesizes speech from text using Google Cloud Text-to-Speech.

    Args:
        request (flask.Request): The incoming request containing the text to synthesize.

    Returns:
        flask.Response: A response containing the synthesized audio data.
    """

    try:
        data = request.get_json()
        
        # Get text, voice gender, and speaking rate from request
        text = data.get('text')
        voiceLanguage = data.get('voiceLanguage')
        speaking_rate = data.get('speakingRate')
        voice_gender = data.get('voiceGender')

        if not text:
            return jsonify({'error': 'No text provided.'}), 400

        # Map speaking rate
        speaking_rate_mapping = {
            'Slow': 0.5,
            'Medium': 1.0,
            'Fast': 2.0,
        }
        speaking_rate = speaking_rate_mapping.get(speaking_rate, 1.0)  # Default to Medium



        # Set up the synthesis input
        synthesis_input = texttospeech.SynthesisInput(text=text)

        # Build the voice request
        voice = texttospeech.VoiceSelectionParams(
            language_code=voiceLanguage, ssml_gender=voice_gender
        )

        # Select the type of audio file you want returned
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.MP3,
            speaking_rate=speaking_rate
        )
        logging.debug(f"{synthesis_input}, {voice}, {audio_config}")
        # Perform the text-to-speech request
        # Initialize Text-to-Speech client
        client = texttospeech.TextToSpeechClient(credentials=credentials)
        retries = 0
        max_retries = 10
        while retries < max_retries:
            try:
                # Attempt to call the API
                response = client.synthesize_speech(input=synthesis_input, voice=voice, audio_config=audio_config)
                return response.audio_content, 200, {'Content-Type': 'audio/mpeg'}
            except GoogleAPICallError as e:
                retries += 1
                logging.error(f"Attempt {retries} failed: {e}")
                if retries >= max_retries:
                    raise
                time.sleep(2 ** retries)  # Exponential backoff
            except RetryError as e:
                retries += 1
                logging.error(f"Attempt {retries} failed: {e}")
                if retries >= max_retries:
                    raise
                time.sleep(2 ** retries)  # Exponential backoff
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/generate_play_by_play/<int:game_pk>/<int:inningNum>', methods=['GET'])
def generate_play_by_play(game_pk, inningNum):
    """
    Generate play-by-play commentary using Gemini.

    Args:
        game_pk: The primary key of the game.
        inningNum: The inning number.

    Returns:
        A JSON response containing the generated commentary.
    """
    try:
        # Fetch game data from the MLB API
        game_data = fetch_game_data(game_pk)
        if not game_data:
            return jsonify({"error": "Could not retrieve game data"}), 500

        # Generate play-by-play commentary using Gemini
        commentary = generate_play_by_play_with_gemini(game_data, inningNum)

        # Return the generated commentary
        return jsonify({
            "message": "Play-by-play commentary generated successfully",
            "commentary": commentary
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def generate_play_by_play_with_gemini(game_data, inningNum):
    """
    Generates play-by-play commentary for a baseball game using Vertex AI's Gemini model.

    Args:
        game_data: The game data to generate commentary from.
        inningNum: The inning number.

    Returns:
        str: The generated play-by-play commentary.
    """

    # Initialize Vertex AI
    vertexai.init(project=f"{PROJECT_ID}", location="us-central1",credentials=credentials)

    inning_data = generate_playbyplay(game_data, inningNum)
    MODEL_ID = "gemini-2.0-flash-exp"  # @param {type:"string", isTemplate: true}


    # Load the generative model
    model = GenerativeModel(
        MODEL_ID,
    )

    # Define generation configuration
    generation_config = {
        "max_output_tokens": 8192,
        "temperature": 1,
        "top_p": 0.95,
    }

    # Define safety settings
    safety_settings = [
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
        SafetySetting(
            category=SafetySetting.HarmCategory.HARM_CATEGORY_HARASSMENT,
            threshold=SafetySetting.HarmBlockThreshold.OFF
        ),
    ]
    
    prompt = f"""Create a play by play announcer script for the {inningNum} inning as if the game was live.
        - Only return the spoken text
        - use raw text format
        - generate at least 1 minutes of audio that covers the top and bottom of the inning
        - If its the top of the first inning give an intro of the game
        - If its the end of the bottom of the inning summarize the plays and score at the end of the inning
        - If its the end of the game summarize the final score and key moments
        - The away team bats at the top of an inning
        - limit reponse to less than 5000 characters
        {inning_data}
    """
        # Generate content
    responses = model.generate_content(
        [prompt],
        generation_config=generation_config,
        safety_settings=safety_settings,
        stream=True,
    )

    # Collect and return the generated responses
    generated_summary = ""
    for response in responses:
        generated_summary += response.text

    return generated_summary

def generate_playbyplay(game_data, inning_num):
    inningIdx = inning_num - 1
    inning = game_data["liveData"]["plays"]["playsByInning"][inningIdx]
    inningNum = inning_num

    playByPlayStrings = []
    playByPlayStrings.append("")

    # Top of the inning
    firstPlayidx = inning["top"][0]
    firstPlay = game_data["liveData"]["plays"]["allPlays"][firstPlayidx]
    firstPlayStr = f"""inning: {firstPlay["about"]["halfInning"]} of Inning {firstPlay["about"]["inning"]}\n 
         inning_start_time: {firstPlay["about"]["startTime"]}\n
         team_at_bat: {game_data["gameData"]["teams"]["away"]["teamName"]}\n
         team_in_field: {game_data["gameData"]["teams"]["home"]["teamName"]}\n
         """
    
    playStr = firstPlayStr

    for playIdx in inning["top"]:
        play = game_data["liveData"]["plays"]["allPlays"][playIdx]
        
        # New code to add player's prior in game at bats.
        player_at_bats = prior_player_at_bats(game_data, player_id=play["matchup"]["batter"]["id"], current_at_bat_index=playIdx)
        summary = in_game_batting_stat_line(game_data, player_at_bats)

        playStr = playStr + f"""batter: {play["matchup"]["batter"]["fullName"]}\n"""
        playStr=playStr + f"""batter stats today so far: {summary}\n""" # In game batting stat line to send to LLM.
        playStr=playStr + f"""pitcher: {play["matchup"]["pitcher"]["fullName"]}\n"""
        for event in play["playEvents"]:
            playStr=playStr + str(event)
        playStr=playStr + f"""result: {play["result"]}\n"""
        
    # Bottom of the inning
    firstPlayidx = inning["bottom"][0]
    firstPlay = game_data["liveData"]["plays"]["allPlays"][firstPlayidx]
    firstPlayStr = f"""inning: {firstPlay["about"]["halfInning"]} of Inning {firstPlay["about"]["inning"]}\n 
         inning_start_time: {firstPlay["about"]["startTime"]}\n
         team_at_bat: {game_data["gameData"]["teams"]["home"]["teamName"]}\n
         team_in_field: {game_data["gameData"]["teams"]["away"]["teamName"]}\n
         """
        
    playStr += firstPlayStr

    for playIdx in inning["bottom"]:
        play = game_data["liveData"]["plays"]["allPlays"][playIdx]

        # New code to add player's prior in game at bats.
        player_at_bats = prior_player_at_bats(game_data, player_id=play["matchup"]["batter"]["id"], current_at_bat_index=playIdx)
        summary = in_game_batting_stat_line(game_data, player_at_bats)

        playStr = playStr + f"""batter: {play["matchup"]["batter"]["fullName"]}\n"""
        playStr=playStr + f"""batter stats today so far: {summary}\n""" # In game batting stat line to send to LLM.
        playStr=playStr + f"""pitcher: {play["matchup"]["pitcher"]["fullName"]}\n"""
        for event in play["playEvents"]:
            playStr=playStr + str(event)
        playStr = playStr + f"""result: {play["result"]}\n"""

    return playStr

# Initialize the Vertex AI SDK using credentials from Secret Manager.
credentials = get_credentials_from_secret_manager(SECRET_NAME, PROJECT_ID)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))