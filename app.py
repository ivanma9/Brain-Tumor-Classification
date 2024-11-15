import os
import streamlit as st
import tensorflow as tf
from tensorflow.keras.models import load_model
from tensorflow.keras.preprocessing import image
import numpy as np
import plotly.graph_objects as go
import cv2
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import Dense, Dropout, Flatten
from tensorflow.keras.optimizers import Adamax
from tensorflow.keras.metrics import Precision, Recall
import google.generativeai as genai
import PIL.Image
from dotenv import load_dotenv
load_dotenv()

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

output_dir = 'saliency_maps'
os.makedirs(output_dir, exist_ok=True)

if not os.path.exists("public/samples"):
    os.makedirs("public/samples", exist_ok=True)

def load_sample_images(folder_path="public/samples"):
    """Load sample images from the public folder"""
    if not os.path.exists(folder_path):
        st.error(f"Sample images folder not found: {folder_path}")
        return []
    
    images = []
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            image_path = os.path.join(folder_path, filename)
            images.append({
                'path': image_path,
                'name': filename,
            })
    return images

def create_sample_gallery():
    """Create a gallery of sample MRI scans"""
    if 'selected_image' not in st.session_state:
        st.session_state.selected_image = None

    st.write("## Sample MRI Scans")
    st.write("Click on any sample image to analyze it, or upload your own below.")
    
    # Load sample images
    sample_images = load_sample_images()

    if not sample_images:
        st.warning("No sample images found in the public/samples folder.")
        return None
    
    # Create a grid layout
    cols = st.columns(4)  # Adjust the number based on how many images per row you want
    
    for idx, img_data in enumerate(sample_images):
        col = cols[idx % 4]
        with col:
            # Load and resize image for thumbnail
            img = PIL.Image.open(img_data['path'])
            img.thumbnail((200, 200))
            
            # Create clickable image
            if st.button(
                label="",
                key=f"sample_{idx}",
                help=f"Click to analyze {img_data['name']}",
            ):
                st.session_state.selected_image = img_data['path']
            
            # Display image
            st.image(
                img,
                caption=img_data['name'].split('.')[0],
                use_column_width=True
            )
    
    return st.session_state.selected_image

def generate_explanation(img_path, model_prediction, confidence):
    # First prompt to generate an initial explanation
    initial_prompt = f"""You are an expert neurologist. You are tasked with explaining a saliency map of a brain tumor MRI scan. The saliency map was generated by a deep learning model that was trained to classify brain tumors as either glioma, meningioma, pituitary, or no tumor.

    The saliency map highlights the regions of the image that the machine learning model is focusing on to make the prediction.

    The deep learning model predicted the image to be of class '{model_prediction}' with a confidence of {confidence * 100}%.

    In your final response:
    - Explain what regions of the brain the model is focusing on, based on the saliency map. Refer to the regions highlighted in light cyan, those are the regions where the model is focusing on.
    - Include all the numbers in this data, and what they represent.

    Let's think step by step about this. Verify step by step.
    """

    img = PIL.Image.open(img_path)
    model = genai.GenerativeModel(model_name="gemini-1.5-flash")
    
    # Generate the initial response
    initial_response = model.generate_content([initial_prompt, img]).text
    
    # Second prompt using the first response to refine the final output
    refinement_prompt = f"""Based on the following expert analysis of the saliency map:
    
    "{initial_response}"
    
    Using this analysis, Please provide a comprehensive report structured with the following sections:

    - **Introduction**: Provide an overview of the saliency map’s purpose, the model’s design and training context, and the importance of interpretability in machine learning-based medical diagnoses.

    - **Data and Methods**: Briefly describe the model’s architecture, including any layers particularly relevant to the prediction, and the process used to generate the saliency map. Explain the significance of the regions highlighted in light cyan and how they relate to tumor detection.

    - **Results**: Interpret the model’s findings based on the highlighted regions in the saliency map. Explain which brain regions are emphasized, their relevance to the predicted tumor type, and how these regions contribute to the model's confidence in the diagnosis.

    - **Conclusion**: Summarize the saliency map’s insights, the model’s confidence in its prediction, and any relevant diagnostic value for clinicians.

    - **Recommendations**: Provide evidence-based recommendations for next steps, including additional diagnostic procedures, consultations, or possible treatment options that may support or refine the model’s prediction. Conclude with any limitations of the model that should be considered in clinical decisions.

    Aim to keep each section concise yet comprehensive, ensuring a total response of no more than 8 sentences. Structure the report to be suitable for review by medical professionals and data scientists, balancing interpretability and technical precision.

    Think through each part step by step and verify each step to ensure clarity, accuracy, and relevance.
    """

    # Generate the refined response based on the second prompt
    refined_response = model.generate_content([refinement_prompt, img]).text
    
    return refined_response


def generate_saliency_map(model, img_array, class_index, img_size, input_file, is_upload=True):
  with tf.GradientTape() as tape:
    img_tensor = tf.convert_to_tensor(img_array)
    tape.watch(img_tensor)
    predictions = model(img_tensor)
    target_class = predictions[:, class_index]

  gradients = tape.gradient(target_class, img_tensor)
  gradients = tf.math.abs(gradients)
  gradients = tf.reduce_max(gradients, axis=-1)
  gradients = gradients.numpy().squeeze()

  # Resize gradients to match original image size
  gradients = cv2.resize(gradients, img_size)

  # Create a circular mask for the brain area
  center = (gradients.shape[0] // 2, gradients.shape[1] // 2)
  radius = min(center[0], center[1]) - 10
  y, x = np.ogrid[:gradients.shape[0], :gradients.shape[1]]
  mask = (x - center[0])**2 + (y - center[1])**2 <= radius**2

  # Apply mask to gradients
  gradients = gradients * mask

  # Normalize only the brain area
  brain_gradients = gradients[mask]
  if brain_gradients.max() > brain_gradients.min():
    brain_gradients = (brain_gradients - brain_gradients.min()) / (brain_gradients.max() - brain_gradients.min())
  gradients[mask] = brain_gradients

  # Apply a higher threshold
  threshold = np.percentile(gradients[mask], 80)
  gradients[gradients < threshold] = 0

  # Apply more aggressive smoothing
  gradients = cv2.GaussianBlur(gradients, (11, 11), 0)

  # Create a heatmap overlay with enhanced contrast
  heatmap = cv2.applyColorMap(np.uint8(255 *gradients), cv2.COLORMAP_JET)
  heatmap = cv2.cvtColor(heatmap, cv2.COLOR_BGR2RGB)

  # Resize heatmap to match original image size
  heatmap = cv2.resize(heatmap, img_size)

  # Superimpose the heatmap on original image with increased opacity
  original_img = image.img_to_array(img)
  superimposed_img = heatmap * 0.7 + original_img * 0.3
  superimposed_img = superimposed_img.astype(np.uint8)

  if is_upload:
        img_path = os.path.join(output_dir, input_file.name)
        with open(img_path, "wb") as f:
            f.write(input_file.getbuffer())
        saliency_map_path = f'saliency_maps/{input_file.name}'
  else:
        sample_filename = os.path.basename(input_file)
        saliency_map_path = f'saliency_maps/{sample_filename}'
    
  # Save the saliency map
  cv2.imwrite(saliency_map_path, cv2.cvtColor(superimposed_img, cv2.COLOR_RGB2BGR))

  return superimposed_img

def load_xception_model(model_path):
  img_shape=(299, 299, 3)
  base_model = tf.keras.applications.Xception(include_top=False, weights="imagenet", input_shape=img_shape, pooling='max')

  model = Sequential([
      base_model,
      Flatten(),
      Dropout(rate=0.3),
      Dense(128, activation='relu'),
      Dropout(rate=0.25),
      Dense(4, activation='softmax')
  ])

  model.build((None,) + img_shape)

  # Compile the model
  model.compile(Adamax(learning_rate=0.001),
                loss='categorical_crossentropy',
                metrics=['accuracy',
                         Precision(),
                         Recall()])

  model.load_weights(model_path)

  return model

st.title("Brain Tumor Classification")

# Add sample gallery
if 'selected_image' not in st.session_state:
    st.session_state.selected_image = None

selected_sample = create_sample_gallery()

if st.session_state.selected_image is not None:
    st.success(f"Analyzing selected image: {os.path.basename(st.session_state.selected_image)}. To avoid rendering issues, please wait for the results to load before selecting another image.")

st.write("Upload an image of a brain MRI scan to classify.")

uploaded_file = st.file_uploader("Choose an image...", type =["jpg", "jpeg", "png"])

if uploaded_file is not None or st.session_state.selected_image is not None:
  
  input_image = uploaded_file if uploaded_file is not None else st.session_state.selected_image

  selected_model = st.radio(
      "Select Model",
      ("Transfer Learning - Xception", "Custom CNN")
  )

  if selected_model == "Transfer Learning - Xception":
    model = load_xception_model('xception_model.weights.h5')
    img_size = (299, 299)
  else:
    model = load_model('cnn_model.h5')
    img_size = (224, 224)

  labels = ['Glioma', 'Meningioma', 'No tumor', 'Pituitary']

  # Load the image (handle both uploaded file and local path)
  if uploaded_file is not None:
    img = image.load_img(uploaded_file, target_size=img_size)
  else:
    img = image.load_img(selected_sample, target_size=img_size) 

  img_array = image.img_to_array(img)
  img_array = np.expand_dims(img_array, axis=0)
  img_array /= 255.0

  with st.spinner('Analyzing image...'):
    prediction = model.predict(img_array)

  # Get the class with the highest probability
  class_index = np.argmax(prediction[0])
  result = labels[class_index]

  st.write(f"Predicted Class: {result}")
  st.write("Predictions:")
  for label, prob in zip(labels, prediction[0]):
    st.write(f"{label}: {prob:.4f}")

  saliency_map = generate_saliency_map(model, img_array, class_index, img_size, uploaded_file if uploaded_file is not None else selected_sample,
    is_upload=uploaded_file is not None)

  col1, col2 = st.columns(2)
  with col1:
    # Display either uploaded file or selected sample
    if uploaded_file is not None:
        st.image(uploaded_file, caption="Uploaded Image", use_container_width=True)
    else:
        st.image(selected_sample, caption="Selected Sample", use_container_width=True)
  with col2:
    st.image(saliency_map, caption="Saliency Map", use_container_width=True)

  st.write("## Classification Results")

  result_container = st.container()
  result_container.markdown(
      f"""
      <div style="background-color: #000000; color: #ffffff; padding: 30px; border-radius: 15px;">
        <div style="display: flex; justify-content: space-between; align-items: center;">
          <div style="flex=1; text-align: center;">
            <h3 style="color: #ffffff; margin-bottom: 10px; font-size: 20px;">Prediction</h3>
            <p style="font-size: 36px; font-weight: 800; color: #FF0000; margin: 0;">
              {result}
            </p>
          </div>
          <div style="width: 2px; height: 80px; background-color: #ffffff; margin: 0 20px;"></div>
          <div style="flex=1; text-align: center;">
            <h3 style="color: #ffffff; margin-bottom: 10px; font-size: 20px;">Confidence</h3>
            <p style="font-size: 36px; font-weight: 800; color: #2196F3; margin 0;">
              {prediction[0][class_index]:.4%}
            </p>
          </div>
        </div>
      </div>
      """,
      unsafe_allow_html=True
  )

  # Prepare data for Plotly chart
  probabilities = prediction[0]
  sorted_indices = np.argsort(probabilities)[::-1]
  sorted_labels = [labels[i] for i in sorted_indices]
  sorted_probabilities = probabilities[sorted_indices]

  # Create a Plotly bar char
  fig = go.Figure(go.Bar(
      x=sorted_probabilities,
      y=sorted_labels,
      orientation='h',
      marker_color=['red' if label == result else 'blue' for label in sorted_labels]
  ))

  # Customize the chart layout
  fig.update_layout(
      title='Probabilities for each class',
      xaxis_title='Probability',
      yaxis_title='Class',
      height=400,
      width=500,
      yaxis=dict(autorange="reversed")
  )

  # Add value labels to the bars
  for i, prob in enumerate(sorted_probabilities):
    fig.add_annotation(
        x=prob,
        y=i,
        text=f'{prob:.4%}',
        showarrow=False,
        xanchor='left',
        xshift=5
    )

  # Display the Plotly chart
  st.plotly_chart(fig)

  if uploaded_file is not None:
    saliency_map_path = f'saliency_maps/{uploaded_file.name}'
  else:
    sample_filename = os.path.basename(selected_sample)
    saliency_map_path = f'saliency_maps/{sample_filename}' 

  with st.spinner('Generating expert explanation...'):
    explanation = generate_explanation(saliency_map_path, result, prediction[0][class_index])
    st.write("## Explanation:")
    st.write(explanation)