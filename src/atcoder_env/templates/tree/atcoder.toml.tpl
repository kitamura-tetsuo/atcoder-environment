[problem]
url = __PROBLEM_URL__

[run]
solution = ["python", "main.py"]
oracle = ["python", "brute.py"]
timeout = 2.0
oracle_timeout = 5.0
output_limit = 1048576
comparison = "tokens"
atol = 1e-9
rtol = 1e-9

[stress]
examples = 200
seed = 0
