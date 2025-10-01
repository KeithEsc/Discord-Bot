import discord
import youtube_dl
from discord.ext import commands
import os
from dotenv import load_dotenv
import json
import re

# --- CONFIGURATION ---
# CONFIRMED ID: The actual bot ID posting the messages (confirmed via !get_message_data)
WORDLE_BOT_ID = 1211781489931452447 
# ---------------------

# Leaderboard data file path
LEADERBOARD_FILE = "leaderboard.json"
# ---------------------

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
intents = discord.Intents.default()
intents.members = True
# CRITICAL: This is required to read the content of messages for non-command events (like on_message)
intents.message_content = True 

client = commands.Bot(command_prefix='!', intents=intents)


def load_leaderboard():
    """Loads the leaderboard data from the JSON file."""
    if not os.path.exists(LEADERBOARD_FILE):
        return {}
    try:
        with open(LEADERBOARD_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # Return empty dictionary if file is missing or corrupted
        return {}

def save_leaderboard(data):
    """Saves the leaderboard data to the JSON file."""
    with open(LEADERBOARD_FILE, 'w') as f:
        json.dump(data, f, indent=4)

def calculate_score(result_str):
    """
    Calculates points based on the Wordle result (e.g., '1/6', '2/6', 'X/6').
    Scoring: 1/6=6 pts, 2/6=5 pts, ..., 6/6=1 pt, X/6=0 pts.
    Formula: 7 - (Number of Guesses)
    """
    if result_str.upper() == 'X':
        return 0  # Failed Wordle
    
    try:
        # Score is based on 7 - (number of guesses)
        guesses = int(result_str)
        return max(0, 7 - guesses) 
    except ValueError:
        return 0

async def process_wordle_message(message, leaderboard_data, client):
    """
    Parses a single Wordle result message and updates the leaderboard data.
    Returns True if scores were processed, False otherwise.
    
    NOTE: This is now an async function to use fetch_user.
    """
    
    # Check 1: Must be the target bot ID.
    if message.author.id != WORDLE_BOT_ID:
        return False

    # CRITICAL FIX: The score data is in message.content, not embeds, so we only parse this.
    content_to_parse = str(message.content)
    
    # We only need to proceed if the content actually exists
    if not content_to_parse:
        return False
        
    # --- NEW CLEANING STEP ---
    # Remove markdown and strip leading/trailing whitespace before parsing
    content_to_parse = content_to_parse.replace('**', '').strip()
    
    scores_logged = 0
    
    # --- FINAL REGEX FIX FOR SCORE MISCALCULATION ---
    # Pattern: 
    # 1. Finds the score: (\d|X)/6:
    # 2. Uses non-greedy capture (.*?) to grab the mentions and stops just before...
    # 3. ...the lookahead (?=...) finds the start of the next score OR the end of the string ($).
    # This prevents the first match from eating the text belonging to the second match.
    WORDLE_SCORE_PATTERN = re.compile(
        r'(\d|X)/6:\s*(.*?)(?=(\d|X)/6:|$)',
        re.IGNORECASE | re.DOTALL
    )

    # Use the compiled pattern for matching
    score_matches = WORDLE_SCORE_PATTERN.findall(content_to_parse)
    
    if not score_matches:
        # Re-introduce simplified debug output for failed messages from the correct bot
        print(f"--- DEBUG: FAILED PARSE ---")
        print(f"ID: {message.id}, Author: {message.author.display_name}")
        print(f"Content: {repr(content_to_parse)}")
        print(f"Reason: Score pattern not found in content (Regex failed).")
        print(f"---------------------------")
        return False

    for match in score_matches:
        # Match is now a tuple: (score_char, mention_segment, [lookahead char or empty string])
        # We only need the first two elements.
        score_char = match[0].upper()
        mention_segment = match[1]
        
        points = calculate_score(score_char)

        # Find all user IDs mentioned in the segment
        user_mentions = re.findall(r'<@!?(\d+)>', mention_segment)
        
        for user_id in user_mentions:
            try:
                # Use fetch_user for reliability, especially during backfill or when users are not cached
                user = await client.fetch_user(int(user_id))
                username = user.name
            except discord.NotFound:
                username = f"User {user_id} (Not Found)"
                print(f"Warning: Could not fetch user {user_id}")
            except Exception as e:
                username = f"User {user_id} (Error)"
                print(f"Error fetching user {user_id}: {e}")
            
            if user_id not in leaderboard_data:
                leaderboard_data[user_id] = {
                    "username": username,
                    "total_score": 0,
                    "games_played": 0
                }

            leaderboard_data[user_id]['total_score'] += points
            leaderboard_data[user_id]['games_played'] += 1
            # Always update the username to the latest display name
            leaderboard_data[user_id]['username'] = username 
            scores_logged += 1
    
    return scores_logged > 0 # Return True if any scores were found and logged


@client.event
async def on_ready():
    print(f'{client.user} has connected to Discord!')


@client.event
async def on_message(message):
    """Handles incoming messages to look for Wordle results."""
    
    # Ignore messages from the bot itself
    if message.author == client.user:
        return

    # Call the processing function for new messages
    leaderboard_data = load_leaderboard()
    
    # Filter by CONFIRMED BOT ID
    if message.author.id == WORDLE_BOT_ID:
        if await process_wordle_message(message, leaderboard_data, client): 
            save_leaderboard(leaderboard_data)
            # Only send success message if it's not the backfill command context
            if not message.content.startswith('!backfill_wordle'): 
                await message.channel.send("Wordle scores processed and leaderboard updated!")

    # Process commands after checking the message content
    await client.process_commands(message)


@client.command(name='wordleboard')
async def display_wordle_leaderboard(ctx):
    """Displays the current Wordle leaderboard."""
    
    leaderboard_data = load_leaderboard()
    
    if not leaderboard_data:
        await ctx.send("The Wordle leaderboard is empty. Wait for the Wordle App bot to post results.")
        return
        
    # Sort the leaderboard by total score (descending)
    sorted_board = sorted(
        leaderboard_data.items(), 
        # Sort primarily by Total Score (descending)
        # Secondary sort by Average Guess (ascending - lower is better)
        key=lambda item: (
            item[1]['total_score'], 
            (7.0 - (item[1]['total_score'] / item[1]['games_played']) if item[1]['games_played'] > 0 else 7.0) 
        ), 
        reverse=True # We reverse for score, but the average guess will be sorted correctly due to the 7.0 subtraction
    )

    embed = discord.Embed(
        title="🏆 Official Wordle Leaderboard 🏆",
        description="Cumulative scores from the daily Wordle results.",
        color=discord.Color.from_rgb(34, 187, 51) # Green color
    )
    
    rank_text = []
    
    for index, (user_id, data) in enumerate(sorted_board):
        rank = index + 1
        username = data.get('username', f"User {user_id}")
        score = data['total_score']
        games = data['games_played']
        
        # --- REVISED AVERAGE GUESS CALCULATION AND DISPLAY ---
        if games > 0:
            avg_points_per_game = score / games
            # Average Guess = 7 - Avg Points (e.g., 7 - 5 pts = 2 guesses)
            avg_guess = 7.0 - avg_points_per_game
            avg_guess_str = f"{avg_guess:.2f}" # Display to two decimal places for precision
        else:
            # Handle players with 0 games (shouldn't happen with current logging logic)
            avg_guess_str = "N/A"
            
        # Check if the player has 0 points (meaning all failed games)
        if score == 0 and games > 0:
            avg_guess_display = "X/6" # Display 'X/6' for players who only failed
        else:
            avg_guess_display = f"{avg_guess_str}/6"
        
        
        # Use emojis for the top 3 spots
        if rank == 1:
            emoji = "🥇"
        elif rank == 2:
            emoji = "🥈"
        elif rank == 3:
            emoji = "🥉"
        else:
            emoji = f"{rank}."
            
        rank_text.append(
            f"{emoji} **{username}**: {score} points, Avg Guess: {avg_guess_display} ({games} games)"
        )

    embed.add_field(name="Ranks", value='\n'.join(rank_text), inline=False)
    embed.set_footer(text="Lower Average Guess is better. Scores: 1/6=6 pts, 6/6=1 pt, X/6=0 pts.")
    
    await ctx.send(embed=embed)


@client.command(name='backfill_wordle')
@commands.has_permissions(administrator=True) 
async def backfill_wordle_leaderboard(ctx, channel: discord.TextChannel = None, limit: int = 5000):
    """
    (Admin only) Scans history for the unique Wordle results phrase and logs the scores to the JSON file.
    """
    if channel is None:
        channel = ctx.channel

    KEYWORD_PHRASE = "yesterday's results"

    await ctx.send(f"🔍 Starting mass scan and logging on **{channel.name}** for the last **{limit}** messages...")
    
    logged_count = 0
    leaderboard_data = load_leaderboard()
    
    try:
        # We use a combined log for efficiency
        
        # Removed the restrictive 'after=ctx.message' argument. 
        # Now, we fetch the history and explicitly filter out the command message itself.
        async for message in channel.history(limit=limit):
            # 1. Skip the command message that initiated the scan
            if message.id == ctx.message.id:
                continue
            
            # 2. Aggressive Content and Author Check
            cleaned_content = message.content.strip().lower()
            
            is_wordle_result = (
                KEYWORD_PHRASE.lower() in cleaned_content and
                message.author.id == WORDLE_BOT_ID
            )
            
            if is_wordle_result:
                # 3. Log the scores for the found message
                if await process_wordle_message(message, leaderboard_data, client):
                    logged_count += 1
                
        # 4. Save the combined data only once after the scan
        if logged_count > 0:
            save_leaderboard(leaderboard_data)
            await ctx.send(
                f"✅ **Mass Logging Complete!** Scanned {limit} messages and successfully logged scores from **{logged_count}** Wordle result posts.\n"
                f"Use `!wordleboard` to see the updated rankings."
            )
        else:
            await ctx.send(
                f"❌ **Mass Logging Complete.** Scanned {limit} messages but found no Wordle result posts. "
                f"Please ensure the Wordle Bot has posted recent results in this channel."
            )
            
    except Exception as e:
        await ctx.send(f"❌ An error occurred during the history scan: {type(e).__name__}: {e}")
        print(f"Error during backfill_wordle scan: {e}")


@client.command(name='log_by_id')
@commands.has_permissions(administrator=True) 
async def log_by_id(ctx, message_id: int):
    """
    (Admin only) Fetches a specific message by ID and logs the Wordle score.
    Usage: !log_by_id 1422814150157013003
    """
    await ctx.send(f"⏳ Attempting to fetch and log message ID: **{message_id}**...")
    
    leaderboard_data = load_leaderboard()

    try:
        # Fetch the single message
        message = await ctx.channel.fetch_message(message_id)
        
        if message.author.id != WORDLE_BOT_ID:
            await ctx.send(f"❌ Error: Message Author ID ({message.author.id}) does not match the configured Wordle Bot ID.")
            return

        # Process the message (this contains the score parsing logic)
        if await process_wordle_message(message, leaderboard_data, client): 
            save_leaderboard(leaderboard_data)
            await ctx.send("✅ **Success!** Wordle scores from that message have been logged and the leaderboard updated.")
        else:
            # The regex failed, print the content to the console for debugging
            print(f"--- DEBUG: FAILED LOGGING ---")
            print(f"ID: {message.id}, Author: {message.author.display_name}")
            print(f"Content: {repr(message.content)}")
            print(f"Reason: Regex failed. Check content above.")
            print(f"---------------------------")
            await ctx.send("⚠️ Warning: Message found, but no valid Wordle scores were extracted. Check console for regex failure debug.")

    except discord.NotFound:
        await ctx.send("❌ Error: Message not found in this channel.")
    except Exception as e:
        await ctx.send(f"❌ An error occurred during logging: {type(e).__name__}: {e}")
        print(f"Log by ID Error: {e}")


# --- Existing Commands (Kept for completeness) ---

@client.command()
async def hello(ctx):
    await ctx.send("Hello, I am EtchoBot. Use the commands !join, !play, and !stop to play music from Youtube, or try !wordleboard for the daily scores!")


@client.command(pass_context=True)
async def join(ctx):
    if (ctx.author.voice):
        channel = ctx.message.author.voice.channel
        await channel.connect()
    else:
        await ctx.send("You need to be in a voice channel to use this command.")


@client.command(pass_context=True)
async def leave(ctx):
    if (ctx.voice_client):
        await ctx.guild.voice_client.disconnect()
        await ctx.send("I left the voice channel.")
    else:
        await ctx.send("I am not in a voice channel.")


@client.command(pass_context=True)
async def stop(ctx):
    voice = discord.utils.get(client.voice_clients, guild=ctx.guild)
    if voice:
        voice.stop()


@client.command(pass_context=True)
async def play(ctx, url: str):
    song_there = os.path.isfile("song.webm")
    try:
        if song_there:
            os.remove("song.webm")
    except PermissionError:
        await ctx.send("Wait for the current playing music to end or use !stop command.")
        return

    # Ensure the bot is connected to a voice channel
    voice = discord.utils.get(client.voice_clients, guild=ctx.guild)
    if not voice:
        if ctx.author.voice:
            voice = await ctx.author.voice.channel.connect()
        else:
            await ctx.send("You need to be in a voice channel or join manually using !join first.")
            return

    ydl_opts = {
        'format': '249/250/251',
        # Removed the 'preferredcodec' part as it was causing issues.
        # youtube_dl is deprecated; for production, consider 'yt-dlp'
        'outtmpl': 'song.webm', # Direct output to song.webm
    }
    
    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            url2 = info_dict.get('url', None) # Get the direct stream URL
            
            # Use streaming if possible to avoid downloading large files
            voice.play(discord.FFmpegPCMAudio(url2, options='-vn'), after=lambda e: print(f'Player error: {e}') if e else None)
            await ctx.send(f"Now playing: {info_dict.get('title', 'Audio Stream')}")
            
    except Exception as e:
        print(f"An error occurred during playback: {e}")
        await ctx.send("Could not play the requested audio.")


client.run(TOKEN)
