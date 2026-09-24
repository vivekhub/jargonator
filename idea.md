The idea is to build a multiplayer corporate game.

2.  The premise of the game is as follows
    1.  it is multiplayer
    2.  Each player, on their turn will, write a simple sentence about themselves
    3.  This sentence is sent to an LLM and it is converted into a complex corporate jargon
    4.  The jargon is presented to all the other players 
    5.  They get 1 minute to guess the original sentence by deciphering the corporate jargon
    6.  Whoever comes closest wins
1.  The game will be played via a slack bot
    1.  Bot managers the players
    1.  Decides  who si the current player - it makes this public knowledge to all players
    1.  Prompts player for a simple sentence about themselves
    1.  The bot then talks to an LLM (openrouter model) for making ti a corporate jargon
    1.  The jargon is then presented by the bot to each player as a DM (except the current player)
    1.  Each one attempts to guess the sentence
    1.  The bot then takes all their inputs
    1.  Finds the closest 3 matches
    1.  Announces the top 3 winners, their answers and the original sentence.
    1.  Top guesser gets 10 points, second 5 points and third 1 point
    1.  Bot keeps score and generates the final score.


