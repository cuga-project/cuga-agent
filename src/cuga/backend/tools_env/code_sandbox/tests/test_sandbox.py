import pytest
from cuga.backend.tools_env.code_sandbox.sandbox import run_local, ExecutionResult

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _enable_run_local(monkeypatch):
    # run_local is gated off by default (GHSA-4rj2 / L-5: unrestricted, unvalidated
    # executor). This suite exercises it directly, so opt in explicitly per test.
    monkeypatch.setenv("CUGA_ALLOW_UNRESTRICTED_RUN_LOCAL", "1")


class TestRunLocal:
    """Test suite for run_local function exception handling."""

    @pytest.mark.asyncio
    async def test_run_local_syntax_error(self):
        """Test run_local with syntax error in code content."""
        code_content = """
print("This is valid")
if True
    print("Missing colon causes syntax error")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert result.stdout == ""
        assert "expected ':'" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_name_error(self):
        """Test run_local with NameError (undefined variable)."""
        code_content = """
print("Starting execution")
print(undefined_variable)  # This will cause NameError
print("This won't be reached")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Starting execution" in result.stdout
        assert "undefined_variable" in result.stderr
        assert "not defined" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_type_error(self):
        """Test run_local with TypeError."""
        code_content = """
print("Before error")
result = "string" + 42  # TypeError: can't concatenate str and int
print("After error")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Before error" in result.stdout
        assert "concatenate str" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_zero_division_error(self):
        """Test run_local with ZeroDivisionError."""
        code_content = """
print("Calculating...")
x = 10
y = 0
result = x / y  # ZeroDivisionError
print(f"Result: {result}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Calculating..." in result.stdout
        assert "division by zero" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_import_error(self):
        """Test run_local with ImportError."""
        code_content = """
print("Attempting import...")
import nonexistent_module  # ImportError/ModuleNotFoundError
print("Import successful")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Attempting import..." in result.stdout
        assert "No module named" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_index_error(self):
        """Test run_local with IndexError."""
        code_content = """
print("Working with list...")
my_list = [1, 2, 3]
print(f"List: {my_list}")
value = my_list[10]  # IndexError
print(f"Value: {value}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Working with list..." in result.stdout
        assert "List: [1, 2, 3]" in result.stdout
        assert "list index out of range" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_key_error(self):
        """Test run_local with KeyError."""
        code_content = """
print("Working with dictionary...")
my_dict = {"a": 1, "b": 2}
print(f"Dict: {my_dict}")
value = my_dict["nonexistent_key"]  # KeyError
print(f"Value: {value}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Working with dictionary..." in result.stdout
        assert "Dict: {'a': 1, 'b': 2}" in result.stdout
        assert "nonexistent_key" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_attribute_error(self):
        """Test run_local with AttributeError."""
        code_content = """
print("Testing attributes...")
my_string = "hello"
print(f"String: {my_string}")
result = my_string.nonexistent_method()  # AttributeError
print(f"Result: {result}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Testing attributes..." in result.stdout
        assert "String: hello" in result.stdout
        assert "no attribute" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_custom_exception(self):
        """Test run_local with custom exception."""
        code_content = """
class CustomError(Exception):
    def __init__(self, message):
        self.message = message
        super().__init__(self.message)

print("Before custom exception")
raise CustomError("This is a custom error message")
print("After custom exception")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Before custom exception" in result.stdout
        assert "This is a custom error message" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_exception_in_function(self):
        """Test run_local with exception inside a function."""
        code_content = """
def problematic_function():
    print("Inside function")
    raise ValueError("Function failed")
    print("This won't print")

print("Before function call")
problematic_function()
print("After function call")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Before function call" in result.stdout
        assert "Inside function" in result.stdout
        assert "Function failed" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_successful_execution(self):
        """Test run_local with successful code execution (no exceptions)."""
        code_content = """
print("Starting successful execution")
x = 10
y = 20
result = x + y
print(f"Result: {result}")
print("Execution completed successfully")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Starting successful execution" in result.stdout
        assert "Result: 30" in result.stdout
        assert "Execution completed successfully" in result.stdout
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_run_local_valid_code_with_calculations(self):
        """Test run_local with valid mathematical calculations."""
        code_content = """
# Basic arithmetic
a = 15
b = 3
print(f"Addition: {a + b}")
print(f"Subtraction: {a - b}")
print(f"Multiplication: {a * b}")
print(f"Division: {a / b}")
print(f"Power: {a ** 2}")

# List operations
numbers = [1, 2, 3, 4, 5]
print(f"Sum: {sum(numbers)}")
print(f"Max: {max(numbers)}")
print(f"Length: {len(numbers)}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Addition: 18" in result.stdout
        assert "Subtraction: 12" in result.stdout
        assert "Multiplication: 45" in result.stdout
        assert "Division: 5.0" in result.stdout
        assert "Power: 225" in result.stdout
        assert "Sum: 15" in result.stdout
        assert "Max: 5" in result.stdout
        assert "Length: 5" in result.stdout
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_run_local_valid_code_with_data_structures(self):
        """Test run_local with valid data structure operations."""
        code_content = """
# Dictionary operations
person = {"name": "Alice", "age": 30, "city": "New York"}
print(f"Name: {person['name']}")
print(f"Age: {person['age']}")
person["occupation"] = "Engineer"
print(f"Keys: {sorted(person.keys())}")

# List comprehension
squares = [x**2 for x in range(1, 6)]
print(f"Squares: {squares}")

# String operations
text = "Hello World"
print(f"Uppercase: {text.upper()}")
print(f"Length: {len(text)}")
print(f"Words: {text.split()}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Name: Alice" in result.stdout
        assert "Age: 30" in result.stdout
        assert "Keys: ['age', 'city', 'name', 'occupation']" in result.stdout
        assert "Squares: [1, 4, 9, 16, 25]" in result.stdout
        assert "Uppercase: HELLO WORLD" in result.stdout
        assert "Length: 11" in result.stdout
        assert "Words: ['Hello', 'World']" in result.stdout
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_run_local_valid_code_with_functions(self):
        """Test run_local with valid function definitions and calls."""
        code_content = """
def greet(name, age=None):
    if age:
        return f"Hello {name}, you are {age} years old!"
    return f"Hello {name}!"

def calculate_area(length, width):
    return length * width

def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

# Test the functions
print(greet("Bob"))
print(greet("Alice", 25))
print(f"Area: {calculate_area(5, 3)}")
print(f"Fibonacci(6): {fibonacci(6)}")

# Lambda function
multiply = lambda x, y: x * y
print(f"Lambda result: {multiply(4, 7)}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Hello Bob!" in result.stdout
        assert "Hello Alice, you are 25 years old!" in result.stdout
        assert "Area: 15" in result.stdout
        assert "Fibonacci(6): 8" in result.stdout
        assert "Lambda result: 28" in result.stdout
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_run_local_valid_code_with_loops_and_conditionals(self):
        """Test run_local with valid loops and conditional statements."""
        code_content = """
# For loop with range
print("Counting:")
for i in range(1, 6):
    print(f"  {i}")

# While loop
count = 0
print("While loop:")
while count < 3:
    print(f"  Count: {count}")
    count += 1

# Conditional statements
numbers = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
even_numbers = []
odd_numbers = []

for num in numbers:
    if num % 2 == 0:
        even_numbers.append(num)
    else:
        odd_numbers.append(num)

print(f"Even numbers: {even_numbers}")
print(f"Odd numbers: {odd_numbers}")

# Nested conditions
score = 85
if score >= 90:
    grade = "A"
elif score >= 80:
    grade = "B"
elif score >= 70:
    grade = "C"
else:
    grade = "F"

print(f"Score {score} gets grade: {grade}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Counting:" in result.stdout
        assert "  1" in result.stdout and "  5" in result.stdout
        assert "While loop:" in result.stdout
        assert "  Count: 0" in result.stdout and "  Count: 2" in result.stdout
        assert "Even numbers: [2, 4, 6, 8, 10]" in result.stdout
        assert "Odd numbers: [1, 3, 5, 7, 9]" in result.stdout
        assert "Score 85 gets grade: B" in result.stdout
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_run_local_valid_code_with_imports(self):
        """Test run_local with valid standard library imports."""
        code_content = """
import math
import datetime
import json

# Math operations
print(f"Pi: {math.pi:.2f}")
print(f"Square root of 16: {math.sqrt(16)}")
print(f"Sine of 30 degrees: {math.sin(math.radians(30)):.2f}")

# DateTime operations
now = datetime.datetime(2023, 5, 15, 12, 30, 45)
print(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Year: {now.year}")

# JSON operations
data = {"name": "John", "age": 30, "active": True}
json_string = json.dumps(data)
print(f"JSON: {json_string}")
parsed_data = json.loads(json_string)
print(f"Parsed name: {parsed_data['name']}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Pi: 3.14" in result.stdout
        assert "Square root of 16: 4.0" in result.stdout
        assert "Sine of 30 degrees: 0.50" in result.stdout
        assert "Current time: 2023-05-15 12:30:45" in result.stdout
        assert "Year: 2023" in result.stdout
        assert '"name": "John"' in result.stdout
        assert "Parsed name: John" in result.stdout
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_run_local_valid_code_with_classes(self):
        """Test run_local with valid class definitions and usage."""
        code_content = """
class Person:
    def __init__(self, name, age):
        self.name = name
        self.age = age
    
    def introduce(self):
        return f"Hi, I'm {self.name} and I'm {self.age} years old."
    
    def have_birthday(self):
        self.age += 1
        return f"Happy birthday! Now I'm {self.age}."

class Student(Person):
    def __init__(self, name, age, grade):
        super().__init__(name, age)
        self.grade = grade
    
    def study(self, subject):
        return f"{self.name} is studying {subject}."

# Create instances
person = Person("Alice", 25)
student = Student("Bob", 20, "A")

print(person.introduce())
print(person.have_birthday())
print(student.introduce())
print(student.study("Python"))
print(f"Student grade: {student.grade}")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Hi, I'm Alice and I'm 25 years old." in result.stdout
        assert "Happy birthday! Now I'm 26." in result.stdout
        assert "Hi, I'm Bob and I'm 20 years old." in result.stdout
        assert "Bob is studying Python." in result.stdout
        assert "Student grade: A" in result.stdout
        assert result.stderr == ""

    @pytest.mark.asyncio
    async def test_run_local_mixed_output_with_exception(self):
        """Test run_local with both stdout and stderr output before exception."""
        code_content = """
import sys

print("This goes to stdout")
print("More stdout content")
sys.stderr.write("This goes to stderr\\n")
print("Even more stdout")
raise RuntimeError("Final error")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "This goes to stdout" in result.stdout
        assert "More stdout content" in result.stdout
        assert "Even more stdout" in result.stdout
        assert "This goes to stderr" in result.stderr
        assert "Final error" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_exception_with_complex_traceback(self):
        """Test run_local with nested function calls creating complex traceback."""
        code_content = """
def level_one():
    print("Level one")
    level_two()

def level_two():
    print("Level two")
    level_three()

def level_three():
    print("Level three")
    raise Exception("Deep nested error")

print("Starting nested execution")
level_one()
print("This won't be reached")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Starting nested execution" in result.stdout
        assert "Level one" in result.stdout
        assert "Level two" in result.stdout
        assert "Level three" in result.stdout
        assert "Deep nested error" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_system_exit_with_code(self):
        """Test run_local with SystemExit exception (exit with code)."""
        code_content = """
print("Before exit")
exit(42)
print("This won't be reached")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 42
        assert "Before exit" in result.stdout
        assert "Generated Code called exit with code : 42" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_system_exit_without_code(self):
        """Test run_local with SystemExit exception (exit without code)."""
        code_content = """
print("Before exit")
exit()
print("This won't be reached")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 0
        assert "Before exit" in result.stdout
        assert "Generated Code called exit with code : 0" in result.stderr

    @pytest.mark.asyncio
    async def test_run_local_quit_function(self):
        """Test run_local with quit() function call."""
        code_content = """
print("Before quit")
quit(1)
print("This won't be reached")
"""
        result = await run_local(code_content)

        assert isinstance(result, ExecutionResult)
        assert result.exit_code == 1
        assert "Before quit" in result.stdout
        assert "Generated Code called exit with code : 1" in result.stderr
