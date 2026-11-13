// Local display fixtures. See README.md for provenance.
window.ANNOTATION_EXAMPLES = [
  {
    "id": "illustrative-workflow",
    "title": "AI at work · Extended context",
    "purpose": "Understand how AI tools affect the participant’s everyday work, decisions, and communication with clients.",
    "source": "Illustrative scenario written for this display preview. Both candidates are illustrative.",
    "history": [
      {
        "speaker": "Interviewer",
        "text": "Could you tell me about your role?"
      },
      {
        "speaker": "Participant",
        "text": "I coordinate client accounts for a small distribution company. Most of my work involves orders, delivery updates, and client emails."
      },
      {
        "speaker": "Interviewer",
        "text": "What does a typical morning look like?"
      },
      {
        "speaker": "Participant",
        "text": "I check overnight orders, look for delays, and send clients updates. Some days that takes an hour, and other days it takes most of the morning."
      },
      {
        "speaker": "Interviewer",
        "text": "Where do AI tools fit into that work?"
      },
      {
        "speaker": "Participant",
        "text": "I sometimes use a chatbot to draft emails. I started a few months ago after a colleague showed me how they use it."
      },
      {
        "speaker": "Interviewer",
        "text": "What made you decide to try it?"
      },
      {
        "speaker": "Participant",
        "text": "Writing the same sort of update several times a day was tiring. I wanted help getting a first draft down."
      },
      {
        "speaker": "Interviewer",
        "text": "How did your first attempts go?"
      },
      {
        "speaker": "Participant",
        "text": "The drafts sounded polished, but I had to change the tone. They were more formal than the way I usually talk to our clients."
      },
      {
        "speaker": "Interviewer",
        "text": "Could you give an example of a change you made?"
      },
      {
        "speaker": "Participant",
        "text": "It wrote a long introduction for a client I speak to every day. I cut that down to a sentence and kept the delivery update."
      },
      {
        "speaker": "Interviewer",
        "text": "How do you decide when to use it now?"
      },
      {
        "speaker": "Participant",
        "text": "If the situation is routine, it can help. For a complaint or a sensitive conversation, I usually write the message myself."
      },
      {
        "speaker": "Interviewer",
        "text": "What makes those situations different for you?"
      },
      {
        "speaker": "Participant",
        "text": "I know the history with the client, and the wording matters. I do not want a draft to promise something we cannot do."
      },
      {
        "speaker": "Interviewer",
        "text": "Have you had to correct a factual detail in a draft?"
      },
      {
        "speaker": "Participant",
        "text": "Yes. Last week it invented a delivery date in a client email. I caught it before sending the message."
      },
      {
        "speaker": "Interviewer",
        "text": "What did you do when you noticed that date?"
      },
      {
        "speaker": "Participant",
        "text": "I checked our order system and corrected the email. Now I check dates, prices, and any promises in a draft."
      },
      {
        "speaker": "Interviewer",
        "text": "How has that affected your workflow?"
      },
      {
        "speaker": "Participant",
        "text": "It saves time on wording, but reviewing facts takes effort. I still use it for routine messages, just with more checking."
      },
      {
        "speaker": "Interviewer",
        "text": "How do you feel about that extra checking?"
      },
      {
        "speaker": "Participant",
        "text": "It is manageable for routine emails. I am still figuring out whether the time saved adds up across a whole week."
      }
    ],
    "target": {
      "speaker": "Interviewer",
      "text": "Thinking about those routine emails, how do you decide whether the draft is ready, how much time do all the checks take, what would your manager think about that, and how has it changed your relationships with clients? Could you walk me through all of that and compare it with how you worked before?",
      "turn_id": "T013"
    },
    "alternative": "Thinking about the last routine email you drafted with AI, could you walk me through what you checked before sending it?"
  },
  {
    "id": "example-T003",
    "title": "AI at work · Trust and assumptions",
    "purpose": "Understand the participant’s experiences of using AI at work.",
    "source": "Transcript excerpt from agentic_judge/examples/interview.txt. The second candidate is illustrative.",
    "history": [
      {
        "speaker": "Interviewer",
        "text": "Tell me about using AI at work.",
        "line": 1
      },
      {
        "speaker": "Participant",
        "text": "I used a chatbot to draft a client email last week, but it invented a delivery date.",
        "line": 2
      },
      {
        "speaker": "Interviewer",
        "text": "What did you do when you noticed that invented date?",
        "line": 3
      },
      {
        "speaker": "Participant",
        "text": "I checked our order system, corrected the email, and decided to verify future drafts.",
        "line": 4
      }
    ],
    "target": {
      "speaker": "Interviewer",
      "text": "You obviously trust AI completely now, right?",
      "line": 5,
      "turn_id": "T003"
    },
    "alternative": "How, if at all, did that experience affect your trust in AI drafts?"
  },
  {
    "id": "example-T004",
    "title": "AI at work · Following up",
    "purpose": "Understand the participant’s experiences of using AI at work.",
    "source": "Transcript excerpt from agentic_judge/examples/interview.txt. The second candidate is illustrative.",
    "history": [
      {
        "speaker": "Interviewer",
        "text": "Tell me about using AI at work.",
        "line": 1
      },
      {
        "speaker": "Participant",
        "text": "I used a chatbot to draft a client email last week, but it invented a delivery date.",
        "line": 2
      },
      {
        "speaker": "Interviewer",
        "text": "What did you do when you noticed that invented date?",
        "line": 3
      },
      {
        "speaker": "Participant",
        "text": "I checked our order system, corrected the email, and decided to verify future drafts.",
        "line": 4
      },
      {
        "speaker": "Interviewer",
        "text": "You obviously trust AI completely now, right?",
        "line": 5
      },
      {
        "speaker": "Participant",
        "text": "No, I still check facts before sending anything.",
        "line": 6
      }
    ],
    "target": {
      "speaker": "Interviewer",
      "text": "What specifically do you check before sending a draft?",
      "line": 7,
      "turn_id": "T004"
    },
    "alternative": "Since AI has made your work so much easier, why do you still spend time checking its drafts?"
  },
  {
    "id": "example-T005",
    "title": "AI at work · Short factual turn",
    "purpose": "Understand the participant’s experiences of using AI at work.",
    "source": "Transcript excerpt from agentic_judge/examples/interview.txt. The second candidate is illustrative.",
    "history": [
      {
        "speaker": "Interviewer",
        "text": "Tell me about using AI at work.",
        "line": 1
      },
      {
        "speaker": "Participant",
        "text": "I used a chatbot to draft a client email last week, but it invented a delivery date.",
        "line": 2
      },
      {
        "speaker": "Interviewer",
        "text": "What did you do when you noticed that invented date?",
        "line": 3
      },
      {
        "speaker": "Participant",
        "text": "I checked our order system, corrected the email, and decided to verify future drafts.",
        "line": 4
      },
      {
        "speaker": "Interviewer",
        "text": "You obviously trust AI completely now, right?",
        "line": 5
      },
      {
        "speaker": "Participant",
        "text": "No, I still check facts before sending anything.",
        "line": 6
      },
      {
        "speaker": "Interviewer",
        "text": "What specifically do you check before sending a draft?",
        "line": 7
      },
      {
        "speaker": "Participant",
        "text": "Dates, prices, and anything promising something to a client.",
        "line": 8
      }
    ],
    "target": {
      "speaker": "Interviewer",
      "text": "What is your job title?",
      "line": 9,
      "turn_id": "T005"
    },
    "alternative": "Could you tell me your job title and describe your responsibilities, reporting structure, career goals, and how your team plans to use AI next year?"
  }
];
