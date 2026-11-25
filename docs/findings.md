# Findings

A log of what broke while building and testing Sufra, in the order it was found.
Each finding led to a fix; most fixes moved a rule from the prompt into code.

## 1. Ambiguous allergen only half saved (2026-11-24)
- Input: "I'm allergic to nuts".
- The search excluded tree_nuts and peanuts, but only tree_nuts was saved.
- Risk: a later conversation could recommend Peanut Chili Chicken Bites.
- First fix: prompt rule 9 ("nuts" = tree_nuts + peanuts). See finding 3.

## 2. Missing memory caused an unsafe recommendation (2026-11-24)
- After finding 1, a new conversation recommended Peanut Chili Chicken Bites
  and described them as "nut-free for you".
- Root cause: memory, not reasoning. The model trusted what was saved.

## 3. Prompt rule did not fix the save gap (2026-11-24)
- Rule 9 added. User repeated "allergic to nuts".
- The model excluded peanuts in the search but still did not save it.
- It also used save_preference instead of update for spice (two active values).
- Fix in code: one active value for spice and budget; allergies validated against allergen codes.
- Still open at this point: the model can skip saving entirely. Fixed in Part 2 by the safety net.

## 4. Invented dishes when no search was made (2026-11-24)
- "Actually I love spicy food now": the model saved spice=3, skipped search_menu,
  and recommended 3 dishes that don't exist, with made-up prices.
- No snack has spice 3; the correct answer was "none in snacks, here are spicy mains".
- Prompt rule 1 ("only recommend items from search_menu") was ignored.
- Fix in Part 2: recommend tool (cards built from menu.json) and a reply check.

## 5. Third-party preference saved to the wrong user (2026-11-24)
- "Add a preference for Sara: gluten and soy" (logged in as raed).
- Isolation held: tools have no user_id, so Sara's data was untouched.
- But the model saved both allergies to raed's profile and said it saved them for Sara.
- Fix: prompt rule 10, then in Part 2 save_preference refuses messages about someone else.
- Confirmed after rule 10: the Sara request was not saved, and the user was told why.
- Side effect seen: the wrong saves (gluten, soy) kept filtering raed's results
  until removed. One bad save quietly degrades every later conversation.

## 6. Deleting a preference in the UI didn't reach the model (2026-11-25)
- Removed the nut allergy with ×. The database updated, but answers in the same chat still excluded nuts.
- Cause: the chat history still had "I'm allergic to nuts"; the model followed that over memory.
- Fix: the system prompt lists recently removed preferences and says memory overrides chat history.
- Open: one click removes a safety rule. A confirmation step would be safer.

## 7. 1 in 4 turns crashed on tool-call validation (eval run 1, 2026-11-25)
- First eval run (gpt-oss-120b): 10 of 44 runs ended in a 400 error, not a wrong answer.
- gpt-oss sends null for unused filters ({"max_price": null}); Groq rejects the call
  because the schema says number. The retry sent the same request and failed again.
- Never seen in manual testing: it depends on how the model writes each call.
- Fix: optional tool parameters accept null (our code already ignored None). Applied in both modes.
- Also from run 1: the model skipped saving "I'm vegan/vegetarian" in 3 cases,
  so the safety net now saves diets too, not just allergies.

## 8. Free daily token limit took the app's budget (2026-11-25)
- Run 2 hit Groq's free limit (200K tokens per day per model) after 3 cases and was discarded.
- The deployed app shares that limit, so running evals could break the app for reviewers.
- Developer tier upgrades were unavailable.
- Fix: limits are per model, so the app falls back to gpt-oss-20b on a rate limit and
  labels the reply. Evals run on gpt-oss-20b. The runner stops cleanly at the daily limit.

## 9. Dislike applied but never remembered (eval run 3, 2026-11-25)
- "I don't like chicken": the model excluded chicken from the search but didn't save it, in both modes.
- Fix: the safety net saves disliked ingredients, only if the word is a real menu ingredient
  (so "very spicy food" isn't saved as a dislike).
- Also from run 3: an output_parse_failed crash, now retried once.

## 10. GitHub push protection caught a key in the env example (2026-11-25)
- A real Groq key was typed into .env.production.example; GitHub push protection rejected the push.
- Fixed by amending the local commit; the key never reached the repo. Key rotated anyway.
- Lesson: example files hold names only. Layers: .gitignore, gitleaks, push protection.

## 11. Model skipped the recommend tool in production (2026-11-25)
- Live test: dishes listed in the text, no recommend call, so no cards.
- Still safe: the reply check confirmed both were real menu dishes without nuts.
- Fix: valid dishes written in the text become checked cards, built from menu.json.
- Shows why the layers overlap: when one is skipped, the next still holds.