# Multilingual Jargonator (parked idea)

Status: **not started**. Captured 2026-09-25 to revisit later.

Goal: each player plays in their mother tongue. The writer writes in their language, each guesser
gets the jargon in their own language and guesses in it.

This is a spec change: `spec.md` §1.3 lists "Languages other than English" as a v1 non-goal.
It would need a v1.2 spec update plus a `DECISIONS.md` entry.

## How it would work

1. **Each player picks a language.** Something like `/jargon language ta`, saved per player.
   Don't rely on the language setting from Slack's `users.info`: that's the language of the Slack
   interface, which is `en-US` for almost everyone.
2. **The writer writes in their own language.** The moderation prompt (`llm/prompts.py`)
   already works on any language. It just needs one line saying the sentence may be in any
   language.
3. **The jargon is made once per language, not once per round.** Today `engine/game_engine.py`
   calls `generate_jargon` once and sends the same text to every guesser. Instead, group
   guessers by language and make one call per language ("rewrite this Tamil sentence as Hindi
   corporate jargon"). With 3 languages in a game, that's 3 calls instead of 1.
4. **Guessers answer in their own language.** The judge compares meaning, not words, so one
   judge call can score a Hindi guess against a Tamil original. Add a rule to the judge prompt:
   "the guess and the sentence may be in different languages; score meaning only."
5. **Results need a translation.** The channel results show the original sentence, which others
   may not be able to read. Add a translation, for example by having the judge or quip call also
   return an English version.

## Code that would break without anyone noticing

- **The leak check in `domain/text.py` only works for English.** It matches `[a-z]+` against an
  English stop-word list. For Tamil, Hindi or Japanese text it finds no words, so the ratio is
  always 0 and nothing is ever flagged as leaky. The check also only makes sense when the writer
  and guesser share a language; across languages, the literal words can't leak. Options:
  - Run it only for English↔English pairs and rely on the prompt for the rest (simplest).
  - Make it Unicode-aware with `\w+` and `casefold()`, but stemming and stop words would still
    be English-only.
  - Have the LLM do the check. That's allowed, since spec v1.1 says all AI work goes through
    the LLM.
- **Word counts** like `truncate_words` and the 60-word jargon limit break for languages written
  without spaces, such as Japanese, Chinese and Thai. Limit by characters, or trust the prompt.
- **The bot's own messages** (about 190 strings in `slack/blocks.py` and `views.py`) are
  English. Translating them properly means a message catalog per language, which is by far the
  most work.

## Suggested order

1. **Content only:** per-player language for sentences, jargon and guesses, with all bot
   messages staying in English. The quip goes out in the channel's language (or English), and
   the leak check runs only for English pairs. This covers about 90% of the fun for maybe 10%
   of the work.
2. **Translated results:** show a translation next to the original in the results.
3. **Full translation** of the bot's messages, only if people actually ask for it.

## Before building

Try it with `make try-llm`: corporate jargon is funny in some languages (Hinglish corporate-speak
is great) and flat in others. Test the actual languages the team speaks first.
