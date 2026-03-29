from priority_parser import classify_request


def classify(prompt_text: str) -> dict:
    return classify_request(prompt_text)
