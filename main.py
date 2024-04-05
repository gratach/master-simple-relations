import sqlite3
from json import loads, dumps
from openai import OpenAI

# Connect to the database
conn = sqlite3.connect("database.sqlite")
cur = conn.cursor()

# Create the tables
cur.execute("CREATE TABLE IF NOT EXISTS triples (id INTEGER PRIMARY KEY, subject TEXT, predicate TEXT, object TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS tripleGeneration (id INTEGER PRIMARY KEY, algorithm TEXT, subject TEXT, predicate TEXT, details TEXT, tripleIds TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS predicates (id INTEGER PRIMARY KEY, predicate TEXT, inversePredicate TEXT, UNIQUE(predicate, inversePredicate))")
cur.execute("CREATE TABLE IF NOT EXISTS predicateGeneration (id INTEGER PRIMARY KEY, algorithm TEXT, predicate TEXT, details TEXT, predicateId INTEGER)")
conn.commit()

def runNavigator(context = None):
    if context == None:
        context = {}
    while not context.setdefault("NavigatorExit", False):
        context.setdefault("NavigatorSession", runNavigatorSession)(context)

def runNavigatorSession(context):
    context.setdefault("DisplayEnvironment", runDisplayEnvironment)(context)
    context.setdefault("NavigatorInput", runNavigatorInput)(context)
    print("\n\n")

def getTermByNumber(number, context):
    return context.setdefault("TermByNumber", {}).get(number)

def getNumberByTerm(term, context):
    numberByTerm = context.setdefault("NumberByTerm", {})
    if term not in numberByTerm:
        numberByTerm[term] = len(numberByTerm) + 1
        context.setdefault("TermByNumber", {})[numberByTerm[term]] = term
    return numberByTerm[term]

def runDisplayEnvironment(context):
    concept = context.setdefault("CurrentConcept", "Physics")
    print(concept)
    # Get all triples with the concept as the subject
    cur.execute("SELECT subject, predicate, object FROM triples WHERE subject = ?", (concept,))
    triples = cur.fetchall()
    # Sort the triples by predicate
    objectsByPredicate = {}
    for triple in triples:
        objectsByPredicate.setdefault(triple[1], []).append(triple[2])
    predicates = [*objectsByPredicate.keys()]
    predicates.sort(key = lambda x: (x[1].upper(), x[1]))
    # Print the triples
    for pred in predicates:
        print("  " + pred + " " + str(getNumberByTerm(pred, context)) + ":")
        objects = objectsByPredicate[pred]
        objects.sort(key = lambda x: (x[1].upper(), x[1]))
        for obj in objects:
            print(f"      {obj} {getNumberByTerm(obj, context)}")

def runNavigatorInput(context):
    prompts = context.setdefault("NavigatorPrompts", [
        navigatorExitPrompt,
        generatePrompt,
        gotoPrompt,
    ])
    inputText = input("Enter a command: ")
    for prompt in prompts:
        if prompt["function"](inputText, context):
            return
    print("Invalid command")
    input("OK")

def tryNavigatorExitPrompt(inputString, context):
    if inputString == "exit":
        context["NavigatorExit"] = True
        return True
    return False
navigatorExitPrompt = {
    "function": tryNavigatorExitPrompt,
    "description": "Exit the navigator",
    "keyword": "exit",
}

def tryGeneratePrompt(inputString, context):
    if not inputString in ("generate", "g"):
        return False
    # Get the current concept
    currentConcept = context.setdefault("CurrentConcept", "Physics")
    # Get the predicate
    pred = input("Enter the predicate: ")
    if pred.isnumeric():
        pred = getTermByNumber(int(pred), context)
    # Check if the predicate is in the database
    cur.execute("SELECT inversePredicate FROM predicates WHERE predicate = ?", (pred,))
    inversePred = cur.fetchone()
    if inversePred != None:
        inversePred = inversePred[0]
    else:
        inversePred = input("Enter the inverse predicate (empty to abort): ")
        if inversePred == "":
            return True
        # Insert the predicate into the database
        cur.execute("INSERT INTO predicates (predicate, inversePredicate) VALUES (?, ?)", (pred, inversePred))
        predId = cur.lastrowid
        cur.execute("INSERT INTO predicates (predicate, inversePredicate) VALUES (?, ?)", (inversePred, pred))
        cur.execute("INSERT INTO predicateGeneration (algorithm, predicate, details, predicateId) VALUES (?, ?, ?, ?)", ("manual", pred, "{}", predId))
        conn.commit()
    # Check if the triples have already been generated
    # TODO
    # Generate the triples
    completion = context["ChatCompletion"]
    query = f'For what concept x is the following true: "{currentConcept}" "{pred}" x ? Name a list of the most relevant concepts x, that are connected in this way to the concept "{currentConcept}". The list should be formatted as a json object ["concept nr 1", "concept nr 2", ...] and contain from 0 to 10 concepts. Return nothing but the list as an answer.'
    seed = context.setdefault("Seed", 0)
    answer = completion(query, seed)
    # Try to parse the answer
    try:
        answer = loads(answer)
    except:
        print("Failed to parse the answer:")
        print(answer)
        return True
    # Insert the triples into the database
    tripleIds = []
    for concept in answer:
        cur.execute("INSERT INTO triples (subject, predicate, object) VALUES (?, ?, ?)", (currentConcept, pred, concept))
        tripleIds.append(cur.lastrowid)
        cur.execute("INSERT INTO triples (subject, predicate, object) VALUES (?, ?, ?)", (concept, inversePred, currentConcept))
    cur.execute("INSERT INTO tripleGeneration (algorithm, subject, predicate, details, tripleIds) VALUES (?, ?, ?, ?, ?)", 
                ("alg1", currentConcept, pred, 
                 dumps({"model": completion.model, "seed": seed, "fingerprint": completion.fingerprint}),
                 ",".join([str(x) for x in tripleIds])))
    conn.commit()
    return True
generatePrompt = {
    "function": tryGeneratePrompt,
    "description": "Generate triples",
    "keyword": "generate",
}

def tryGotoPrompt(inputString, context):
    if inputString.isnumeric():
        concept = getTermByNumber(int(inputString), context)
    elif not inputString in ("goto", "g"):
        return False
    else:
        # Get the concept
        concept = input("Enter the concept: ")
        if concept.isnumeric():
            concept = getTermByNumber(int(concept), context)
    context["CurrentConcept"] = concept
    return True
gotoPrompt = {
    "function": tryGotoPrompt,
    "description": "Go to a concept",
    "keyword": "goto",
}
        
class ChatCompletion:
    """
    A class to wrap the OpenAI chat completion.
    """
    def __init__(self, client, model):
        self.client = client
        self.model = model
    def __call__(self, query, seed = 0):
        answer = self.client.chat.completions.create(
            model=self.model,
            seed = seed,
            messages=[
                {
                    "role": "system",
                    "content": "User: " + query
                }
            ]
        )
        self.fingerprint = answer.system_fingerprint
        return answer.choices[0].message.content

context  = {
    "ChatCompletion": ChatCompletion(OpenAI(), "gpt-3.5-turbo"),
}


def writeSubtopicTreeFile():
    # Open the text file for writing
    with open("subtopic_tree.txt", "w") as file:
        # Write the header
        file.write("Subtopic Tree\n\n")
        # Start with the root concept "Physics"
        root_concept = "Physics"
        # Call the recursive function to build the subtopic tree
        buildSubtopicTree(root_concept, file, set())

def buildSubtopicTree(concept, file, alreadySearchedConcepts, indent=0):
    # Check if the current concept has already been searched
    if concept in alreadySearchedConcepts:
        file.write("  " * indent + concept + " -> *\n")
        return
    # Add the current concept to the set of already searched concepts
    alreadySearchedConcepts.add(concept)
    # Get all subtopics of the current concept from the database
    cur.execute("SELECT object FROM triples WHERE subject = ? AND predicate = 'has specific subtopic'", (concept,))
    subtopics = cur.fetchall()
    # Write the current concept and its subtopics to the file
    file.write("  " * indent + concept + "\n")
    for subtopic in subtopics:
        buildSubtopicTree(subtopic[0], file, alreadySearchedConcepts, indent + 1)

#writeSubtopicTreeFile()
runNavigator(context)