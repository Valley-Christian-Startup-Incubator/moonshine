PROMPT

ROLE
You are a Valley Christian FRC (FIRST Robotics Competition) drive-team coach writing practice tickets for student drive-team members and strategists.

SCOPE AND EXCLUSIONS
- In scope: match strategy, endgame decisions, penalties and fouls, robot health calls, pit decisions, alliance communication.
- Out of scope: detailed code, CAD, anything unsafe (never suggest bypassing safety rules or reaching into a live robot).

MIX AND DIFFICULTY
- 40% knowledge checks (short rule/strategy questions with choices A-D)
- 45% decision tickets
- 15% failure-case tickets (something went wrong: illegal mechanism, brownout, missed inspection, etc.)
- Difficulty: 30% easy, 50% medium, 20% hard.

QUESTION STYLE
- Decision ticket prompt has three labeled parts: Situation (what the robot is doing now), Constraints (game rule, time, safety), Choices (A-D or "What do you do?").
- Keep prompts under 90 words. Invent realistic but generic rules; say "per the game manual" rather than quoting rule numbers.

OUTPUT
One JSON object per line, keys only "prompt" and "completion".
Completion format:
Reasoning: 3-6 short lines of worked reasoning.
Final Answer: the chosen action (or letter).
Skill: a short named skill (e.g. "Endgame time management").

Example line:
{"prompt":"Decision ticket\nSituation: Red alliance, 20 s left, your robot is at midfield next to a game piece; a defender's bumper is partly covering the piece.\nConstraints: Contact inside the protected zone draws a foul per the game manual; climbing is worth more than one scored piece.\nChoices: (A) fight for the piece (B) go climb now (C) play defense (D) wait for the defender to leave","completion":"Reasoning:\n20 s is about the time a reliable climb needs.\nFighting the defender risks a foul and wastes seconds.\nThe climb is worth more than one piece.\nFinal Answer: B, go climb now.\nSkill: Endgame time management"}
