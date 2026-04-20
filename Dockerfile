FROM python:3.11-slim

# Install uv inside the container
RUN pip install uv

WORKDIR /app

# Copy your (fixed) requirements file and install dependencies globally in the container
COPY requirements.txt .
RUN apt-get update && apt-get install -y curl && uv pip install --system -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose the port Uvicorn will run on
EXPOSE 8000

# Start Uvicorn bound to all interfaces
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
