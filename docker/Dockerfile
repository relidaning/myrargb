FROM python:3.12-slim

# Install dependencies required by Chrome
RUN apt-get update && apt-get install -y \
    wget gnupg unzip curl \
    libnss3 libasound2 libgbm1 libx11-6 libxcomposite1 \
    libxdamage1 libxrandr2 libxext6 libxi6 libxrender1 \
    libatk1.0-0 libatk-bridge2.0-0 libpango-1.0-0 \
    libcups2 libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
    | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable

# Install ChromeDriver (matching version)
RUN JSON=$(curl -s https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json) \
    && DRIVER_URL=$(echo $JSON | grep -o "https[^\"']*chromedriver-linux64.zip" | head -1) \
    && wget -q "$DRIVER_URL" -O chromedriver.zip \
    && unzip chromedriver.zip \
    && mv chromedriver-linux64/chromedriver /usr/local/bin/chromedriver \
    && chmod +x /usr/local/bin/chromedriver \
    && rm -rf chromedriver.zip chromedriver-linux64

# Increase shared memory size
RUN mkdir -p /dev/shm && chmod 1777 /dev/shm

# Install Python deps
COPY requirements.txt .
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . /app
WORKDIR /app

CMD ["python", "app.py"]
