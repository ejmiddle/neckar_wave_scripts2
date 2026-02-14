"""
Robust LLM-based entity extraction using the
'find entities → populate fields' pattern.

Requirements:
- pydantic>=2
- openai (or compatible client)
"""

from datetime import date
from typing import List, Optional

from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

# ------------------------------------------------------------------------------
# 1. Define the schema (this is the contract)
# ------------------------------------------------------------------------------

class Person(BaseModel):
    name: str = Field(description="Full name as explicitly mentioned")
    role: Optional[str] = Field(description="Role or title if explicitly stated")
    organization: Optional[str] = Field(description="Organization affiliation if stated")

class Event(BaseModel):
    title: Optional[str]
    date: Optional[date]
    location: Optional[str]
    participants: List[Person]


# ------------------------------------------------------------------------------
# 2. Prompts (short, mechanical, non-creative)
# ------------------------------------------------------------------------------

SYSTEM_PROMPT = """
You extract structured data.
Populate the given schema from the text.
If a field is not explicitly stated, return null.
Do not infer or guess.
"""

TEXT = """
Apple CEO Tim Cook met with French President Emmanuel Macron in Paris on June 10, 2024
to discuss AI regulation and investment.
"""


# ------------------------------------------------------------------------------
# 3. LLM client
# ------------------------------------------------------------------------------

client = OpenAI()


# ------------------------------------------------------------------------------
# 4. Tool (function) definition using the schema
# ------------------------------------------------------------------------------

TOOLS = [{
    "type": "function",
    "function": {
        "name": "extract_event",
        "description": "Extract an Event object from text",
        "parameters": Event.model_json_schema(),
    }
}]


# ------------------------------------------------------------------------------
# 5. Single extraction attempt
# ------------------------------------------------------------------------------

def extract_once(messages):
    response = client.chat.completions.create(
        model="gpt-4.1",
        temperature=0,
        messages=messages,
        tools=TOOLS,
        tool_choice={
            "type": "function",
            "function": {"name": "extract_event"}
        }
    )
    return response.choices[0].message.tool_calls[0].function.arguments


# ------------------------------------------------------------------------------
# 6. Extract → validate → repair loop
# ------------------------------------------------------------------------------

messages = [
    {"role": "system", "content": SYSTEM_PROMPT},
    {"role": "user", "content": f"Text:\n{TEXT}"}
]

MAX_RETRIES = 2

for attempt in range(MAX_RETRIES + 1):
    raw_args = extract_once(messages)

    try:
        event = Event.model_validate_json(raw_args)
        break  # success
    except ValidationError as e:
        if attempt == MAX_RETRIES:
            raise RuntimeError("Extraction failed after retries") from e

        # Ask the model to repair ONLY what is invalid
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"""
The following JSON failed validation:

{raw_args}

Validation error:
{e}

Fix ONLY the invalid fields.
Return a corrected version that fully matches the schema.
"""
            }
        ]


# ------------------------------------------------------------------------------
# 7. Result
# ------------------------------------------------------------------------------

print(event.model_dump())
