const uploadForm = document.getElementById("upload-form");
const askForm = document.getElementById("ask-form");
const chatBox = document.getElementById("chat-box");
const questionInput = document.getElementById("question-input");
const uploadStatus = document.getElementById("upload-status");
const docName = document.getElementById("doc-name");
const docPages = document.getElementById("doc-pages");
const spinner = document.getElementById("spinner");

function addMessage(text, type = "bot", sources = []) {
  const msg = document.createElement("div");
  msg.className = `message ${type}`;
  msg.textContent = text;

  if (sources.length > 0) {
    const src = document.createElement("span");
    src.className = "source";
    src.textContent =
      "Source: " +
      sources.map((s) => `File ${s.file_name}, Page ${s.page}`).join(" | ");
    msg.appendChild(src);
  }

  chatBox.appendChild(msg);
  chatBox.scrollTop = chatBox.scrollHeight;
}

function setLoading(isLoading) {
  spinner.classList.toggle("hidden", !isLoading);
}

uploadForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("pdf-file");

  if (!fileInput.files.length) {
    uploadStatus.textContent = "Please choose a PDF file first.";
    return;
  }

  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  uploadStatus.textContent = "Processing PDF...";

  try {
    const response = await fetch("/upload", {
      method: "POST",
      body: formData,
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Failed to upload PDF.");
    }

    uploadStatus.textContent = data.message;
    docName.textContent = `File: ${data.file_name}`;
    docPages.textContent = `Pages: ${data.pages}`;
    addMessage(`Loaded document ${data.file_name} (${data.pages} pages).`, "bot");
  } catch (err) {
    uploadStatus.textContent = err.message;
  }
});

askForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = questionInput.value.trim();

  if (!question) return;

  addMessage(question, "user");
  questionInput.value = "";
  setLoading(true);

  try {
    const response = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "Failed to fetch answer.");
    }

    addMessage(data.answer, "bot", data.sources || []);
  } catch (err) {
    addMessage(`Error: ${err.message}`, "bot");
  } finally {
    setLoading(false);
  }
});
