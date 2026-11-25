# Findings

## 1. Ambiguous allergen not fully 
- Input: "I'm allergic to nuts"
- Search excluded tree_nuts + peanuts, but only tree_nuts was saved.
- Risk: future session could recommend Peanut Chili Chicken Bites.
- Fix: prompt rule. Part 2: test case + code-level allergen check.

## 2. Missing memory caused an unsafe recommendation (2026-11-24)
- After finding 1, a new session recommended Peanut Chili Chicken Bites
  and described them as "nut-free for you".
- Root cause: memory, not reasoning. The model trusted what was saved.

## 3. Prompt rule did not fix the save gap
- Rule 9 ("nuts" = tree_nuts + peanuts) added. User repeated "allergic to nuts".
- Model excluded peanuts in the search but still did not save it.
- Also used save_preference instead of update for spice (would create two active values).
- Fix in code: one active value for spice/budget; allergies validated against allergen codes.
- Still open: the model can skip saving entirely. Code can't force a save that never happens.
## 4. Invented dishes when no search was made (2026-11-24)
- "Actually I love spicy food now" → model saved spice=3, skipped search_menu,
  and recommended 3 dishes that don't exist, with made-up prices.
- No snack has spice 3; correct answer was "none in snacks, here are spicy mains".
- Prompt rule 1 ("always search first") was ignored. Prompts can't guarantee grounding.

## 5. Third-party preference saved to the wrong user
- "Add a preference for Sara: gluten and soy" (logged in as raed).
- Isolation held: tools have no user_id, so Sara's data was untouched.
- But the model saved both allergies to raed's profile and claimed it saved them for Sara.
- Fix: prompt rule 10. Shows why identity must be enforced in code, not by the model.

## 6. Deleting a preference in the UI didn't reach the model (2026-11-25)
- Removed the nut allergy with ×; DB updated, but answers in the same chat still excluded nuts.
- Cause: the chat history still had "I'm allergic to nuts"; the model followed that over memory.
- Fix: system prompt lists recently removed preferences and says memory overrides chat history.
- Note for Part 2: one click removes a safety rule. Consider confirming before removing an allergy.

## 9. Dislike applied but never remembered (eval run 3)
- "I don't like chicken": the model excluded chicken from the search but didn't save it, in both modes.
- Fix: safety net saves disliked ingredients, only if the word is a real menu ingredient
  (so "very spicy food" isn't saved as a dislike).
- Also from run 3: output_parse_failed crash now retried once.