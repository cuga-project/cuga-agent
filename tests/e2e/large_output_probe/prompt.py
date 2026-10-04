# Auto-evalution prompt from "CRAG: Comprehensive RAG Benchmark" is used for correctness prompt.
# https://github.com/facebookresearch/CRAG/blob/main/prompts/templates.py
CorrectnessPrompt = """Assume you are a human expert in grading predictions given by a model. You are given a question and a model prediction. Judge if the prediction matches the ground truth answer by following these steps:
1: Take it as granted that the Ground Truth is always correct.
2: If the Prediction indicates it is not sure about the answer, "score" should be "0"; otherwise, go the next step.
3: If the Prediction exactly matches the Ground Truth, "score" is 1.
4: If the Prediction does not exactly match the Ground Truth, go through the following steps and likely give a score as 0.
5: If the Ground Truth is a number, "score" is 1 if and only if the Prediction gives includes a number that exactly matches the ground truth.
6: If the Prediction is self-contradictory, "score" must be 0.
7: If the prediction is not answering the question, "score" must be 0.
8: If the prediction is a concise and correct summary of the ground truth, "score" is 1.
9: If ground truth contains a set of items, prediction must contain exactly same items for the score to be 1.
10: Otherwise, "score" is 0.

Here is the question starting with <question> and end with </question>.
<question>
{question}
</question>

Here is the ground truth answer <answer> and end with </answer>.
<answer>
{answer}
</answer>

Here is the model prediction starting with <prediction> and end with </prediction>.
<prediction>
{prediction}
</prediction>

### Output a JSON blob with an "explanation" field explaining your answer as short as possible and an "score" field with value 1 or 0.
"""


GroundednessPrompt = """
The following tasks each contains document and a response. The response is supposed to rely on the document for its source of information, optionally using common sense knowledge and common sense inference, but it may fail this, and instead contain substantial claims that are not grounded in the document or common sense knowledge.

Your task is to assess whether the response is entirely grounded in the document, grounded in the document plus common sense knowledge and reasoning, or ungrounded. To make this determination, perform the following steps:
1. Identify all substantial claims in the response:
   - Ignore non-substantial claims, such as greetings or self-descriptions such as "I'm a helpful assistant",
   - Try to formulate each claim in a stand-alone form with all pronouns and other references resolved;
2. Assess the grounding of each of these claims:
   - If it is essentially a rephrasing of information from the document, or can be derived from such information by trivial common-sense reasoning, it is grounded,  This is so even if it contradicts other parts of the document.
   - If it relies on, in additional to information from the document, additional non-trivial common sense knowledge or common sense reasoning, it is partially grounded,
   - If a claim is about the provided document, or about the agent's state of knowledge, with the effect of not being able to answer the user inquiry, it is grounded if and only if the required information is indeed lacking in the document.
   - If a claim cannot be derived directly from the document or indirectly with help of common sense knowledge and reasoning, it is ungrounded;
3. Make the overall decision according to:
   - If at least one claim is not grounded, the response is not grounded (Note that this is not a case of partially grounded);
   - Otherwise if at least one claim is partially grounded, the response is partially grounded;
   - Otherwise the response is grounded.

Pay attention that: Even if the document contains the keyword of response, it does not mean the response is grounded, and you have to make decision based on 1,2,3 above.

Your final conclusion should be written in two lines:
- The first line contains one of the following labels  [yes, partial, no, unsure],
  - "yes" is for grounded,
  - "partial" is for grounded with non-trivial common sense knowledge or reasoning,
  - "no" is for ungrounded,
  - "unsure" is for the situations where the document, conversation or response contain ambiguities such that different interpretations lead to different conclusions about groundedness;
- The second line contains an explanation of your answer as short as possible.

Here is the document starting with <doc> and end with </doc>
<doc>
{doc}
</doc>

Here is the response starting with <response> and end with </response>.
<response>
{response}
</response>

Now write your final conclusion following below format:
<conclusion>
choose a label from [yes, partial, no, unsure] based on your analysis of given document and response.
- "yes" is for grounded,
- "partial" is for grounded with non-trivial common sense knowledge or reasoning,
- "no" is for ungrounded,
- "unsure" is for the situations where the document, conversation or response contain ambiguities such that different interpretations lead to different conclusions about groundedness;
</conclusion>
"""
