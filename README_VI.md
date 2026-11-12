<div align="center">
  <h2><b>Kronos: Mô hình Nền tảng cho Ngôn ngữ Thị trường Tài chính</b></h2>
</div>

<div align="center">

<a href="https://huggingface.co/NeoQuasar"> 
<img src="https://img.shields.io/badge/🤗-Hugging_Face-yellow" alt="Hugging Face"> 
</a> 
<a href="https://shiyu-coder.github.io/Kronos-demo/"> <img src="https://img.shields.io/badge/🚀-Live_Demo-brightgreen" alt="Live Demo"> </a>
<a href="https://github.com/shiyu-coder/Kronos/graphs/commit-activity"> 
<img src="https://img.shields.io/github/last-commit/shiyu-coder/Kronos?color=blue" alt="Last Commit"> 
</a> 
<a href="https://github.com/shiyu-coder/Kronos/stargazers"> 
<img src="https://img.shields.io/github/stars/shiyu-coder/Kronos?color=lightblue" alt="GitHub Stars"> 
</a> 
<a href="https://github.com/shiyu-coder/Kronos/network/members"> 
<img src="https://img.shields.io/github/forks/shiyu-coder/Kronos?color=yellow" alt="GitHub Forks"> 
</a> 
<a href="./LICENSE"> 
<img src="https://img.shields.io/github/license/shiyu-coder/Kronos?color=green" alt="License"> 
</a>

</div>

<div align="center">
  <a href="./README.md">English</a> | 
  <a href="./README_VI.md">Tiếng Việt</a> | 
  <a href="https://zdoc.app/de/shiyu-coder/Kronos">Deutsch</a> | 
  <a href="https://zdoc.app/es/shiyu-coder/Kronos">Español</a> | 
  <a href="https://zdoc.app/fr/shiyu-coder/Kronos">Français</a> | 
  <a href="https://zdoc.app/ja/shiyu-coder/Kronos">日本語</a> | 
  <a href="https://zdoc.app/ko/shiyu-coder/Kronos">한국어</a> | 
  <a href="https://zdoc.app/pt/shiyu-coder/Kronos">Português</a> | 
  <a href="https://zdoc.app/ru/shiyu-coder/Kronos">Русский</a> | 
  <a href="https://zdoc.app/zh/shiyu-coder/Kronos">中文</a>
</div>

<p align="center">

<img src="./figures/logo.png" width="100">

</p>

> Kronos là **mô hình nền tảng mã nguồn mở đầu tiên** dành riêng cho chuỗi nến tài chính (K-lines), 
> được huấn luyện trước trên dữ liệu từ hơn **45 sàn giao dịch trên toàn cầu**.

</div>

## 📰 Tin tức (News)
*   🚩 **[10.11.2025]** Kronos đã được chấp nhận tại hội nghị AAAI 2026.
*   🚩 **[17.08.2025]** Phát hành các script hỗ trợ fine-tuning! Hãy khám phá để tùy biến Kronos cho bài toán riêng của bạn.
*   🚩 **[02.08.2025]** Bài báo khoa học của dự án hiện đã có mặt trên [arXiv](https://arxiv.org/abs/2508.02739)!

<p align="center">

## 📜 Giới thiệu (Introduction)

**Kronos** là một họ các mô hình nền tảng decoder-only, được huấn luyện trước chuyên biệt cho "ngôn ngữ" của thị trường tài chính — các chuỗi nến K-line. Khác với các mô hình nền tảng chuỗi thời gian tổng quát (TSFM), Kronos được thiết kế để xử lý các đặc tính riêng biệt và độ nhiễu cao của dữ liệu tài chính. Mô hình hoạt động dựa trên khung kiến trúc 2 giai đoạn mới lạ:
1. Một tokenizer chuyên biệt trước tiên sẽ lượng tử hóa dữ liệu nến liên tục, đa chiều (OHLCV) thành **các token phân cấp rời rạc**.
2. Một Transformer tự hồi quy (autoregressive) quy mô lớn sau đó được huấn luyện trước trên các token này, cho phép nó đóng vai trò là một mô hình hợp nhất cho đa dạng tác vụ định lượng (quant).

<p align="center">
    <img src="figures/overview.png" alt="" align="center" width="700px" />
</p>

## ✨ Bản Demo Trực tiếp (Live Demo)
Chúng tôi đã triển khai một trang demo trực tiếp để trực quan hóa kết quả dự báo của Kronos. Trang web hiển thị dự báo cho cặp giao dịch **BTC/USDT** trong vòng 24 giờ tiếp theo.

**👉 [Truy cập Bản Demo Trực tiếp tại đây](https://shiyu-coder.github.io/Kronos-demo/)**

## 📦 Bộ Sưu tập Mô hình (Model Zoo)
Chúng tôi phát hành một loạt các mô hình đã được huấn luyện sẵn với dung lượng khác nhau để đáp ứng các nhu cầu ứng dụng và tài nguyên tính toán khác nhau. Tất cả mô hình đều có thể tải trực tiếp từ Hugging Face Hub.

| Mô hình (Model) | Tokenizer                                                                       | Chiều dài ngữ cảnh (Context length) | Số tham số (Params) | Mã nguồn mở (Open-source)                                                |
|-----------------|---------------------------------------------------------------------------------|-------------------------------------|---------------------|---------------------------------------------------------------------------|
| Kronos-mini     | [Kronos-Tokenizer-2k](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-2k)     | 2048                                | 4.1M                | ✅ [NeoQuasar/Kronos-mini](https://huggingface.co/NeoQuasar/Kronos-mini)   |
| Kronos-small    | [Kronos-Tokenizer-base](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base) | 512                                 | 24.7M               | ✅ [NeoQuasar/Kronos-small](https://huggingface.co/NeoQuasar/Kronos-small) |
| Kronos-base     | [Kronos-Tokenizer-base](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base) | 512                                 | 102.3M              | ✅ [NeoQuasar/Kronos-base](https://huggingface.co/NeoQuasar/Kronos-base)   |
| Kronos-large    | [Kronos-Tokenizer-base](https://huggingface.co/NeoQuasar/Kronos-Tokenizer-base) | 512                                 | 499.2M              | ❌                                                                         |


## 🚀 Hướng dẫn Bắt đầu (Getting Started)

### Cài đặt Môi trường

1. Cài đặt Python 3.10+, sau đó cài đặt các thư viện phụ thuộc:

```shell
pip install -r requirements.txt
```

### 📈 Thực hiện Dự báo (Making Forecasts)

Dự báo với Kronos rất đơn giản thông qua lớp `KronosPredictor`. Lớp này xử lý việc tiền xử lý dữ liệu, chuẩn hóa (normalization), dự đoán và giải chuẩn hóa (inverse normalization), giúp bạn đi từ dữ liệu thô đến kết quả dự báo chỉ trong vài dòng code.

**Lưu ý quan trọng**: Giá trị `max_context` cho `Kronos-small` và `Kronos-base` là **512**. Đây là độ dài chuỗi tối đa mô hình có thể xử lý. Để đạt hiệu năng tối ưu, bạn nên đảm bảo độ dài dữ liệu đầu vào (tức `lookback`) không vượt quá giới hạn này. `KronosPredictor` sẽ tự động cắt ngắn (truncate) dữ liệu nếu ngữ cảnh dài hơn.

Dưới đây là hướng dẫn từng bước để tạo dự báo đầu tiên của bạn:

#### 1. Tải Tokenizer và Mô hình

Trước tiên, tải mô hình Kronos đã huấn luyện trước cùng tokenizer tương ứng từ Hugging Face Hub:

```python
from model import Kronos, KronosTokenizer, KronosPredictor

# Tải từ Hugging Face Hub
tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
```

#### 2. Khởi tạo Predictor

Tạo một thực thể của `KronosPredictor`, truyền vào model, tokenizer và thiết bị (device) bạn muốn chạy:

```python
# Khởi tạo predictor
predictor = KronosPredictor(model, tokenizer, max_context=512)
```

#### 3. Chuẩn bị Dữ liệu Đầu vào

Phương thức `predict` yêu cầu 3 đầu vào chính:
-   `df`: Một pandas DataFrame chứa dữ liệu nến K-line lịch sử. Bắt buộc phải có các cột `['open', 'high', 'low', 'close']`. Cột `volume` và `amount` là tùy chọn.
-   `x_timestamp`: Một pandas Series chứa các mốc thời gian (timestamps) tương ứng với dữ liệu lịch sử trong `df`.
-   `y_timestamp`: Một pandas Series chứa các mốc thời gian cho các chu kỳ tương lai mà bạn muốn dự đoán.

```python
import pandas as pd

# Đọc dữ liệu
df = pd.read_csv("./data/XSHG_5min_600977.csv")
df['timestamps'] = pd.to_datetime(df['timestamps'])

# Định nghĩa độ dài cửa sổ ngữ cảnh và độ dài dự đoán
lookback = 400
pred_len = 120

# Chuẩn bị dữ liệu đầu vào cho predictor
x_df = df.loc[:lookback-1, ['open', 'high', 'low', 'close', 'volume', 'amount']]
x_timestamp = df.loc[:lookback-1, 'timestamps']
y_timestamp = df.loc[lookback:lookback+pred_len-1, 'timestamps']
```

#### 4. Tạo Dự báo 

Gọi phương thức `predict` để sinh kết quả dự báo. Bạn có thể kiểm soát quá trình lấy mẫu (sampling) bằng các tham số như `T`, `top_p`, và `sample_count` để dự báo xác suất.

```python
# Tạo dự đoán
pred_df = predictor.predict(
    df=x_df,
    x_timestamp=x_timestamp,
    y_timestamp=y_timestamp,
    pred_len=pred_len,
    T=1.0,          # Temperature cho việc lấy mẫu
    top_p=0.9,      # Xác suất lấy mẫu nucleus (nucleus sampling)
    sample_count=1  # Số lượng đường dự báo sinh ra để tính trung bình
)

print("Dữ liệu dự báo (5 dòng đầu):")
print(pred_df.head())
```

Phương thức `predict` trả về một pandas DataFrame chứa các giá trị dự báo cho `open`, `high`, `low`, `close`, `volume`, và `amount`, với chỉ mục (index) là các mốc thời gian `y_timestamp` bạn đã cung cấp.

Để xử lý hiệu quả nhiều chuỗi thời gian cùng lúc, Kronos cung cấp phương thức `predict_batch` cho phép dự báo song song trên nhiều bộ dữ liệu. Tính năng này đặc biệt hữu ích khi bạn cần dự báo đồng thời cho nhiều mã tài sản hoặc nhiều khoảng thời gian:

```python
# Chuẩn bị nhiều tập dữ liệu để dự báo hàng loạt (batch prediction)
df_list = [df1, df2, df3]  # Danh sách các DataFrame
x_timestamp_list = [x_ts1, x_ts2, x_ts3]  # Danh sách mốc thời gian lịch sử
y_timestamp_list = [y_ts1, y_ts2, y_ts3]  # Danh sách mốc thời gian tương lai

# Tạo dự báo hàng loạt
pred_df_list = predictor.predict_batch(
    df_list=df_list,
    x_timestamp_list=x_timestamp_list,
    y_timestamp_list=y_timestamp_list,
    pred_len=pred_len,
    T=1.0,
    top_p=0.9,
    sample_count=1,
    verbose=True
)

# pred_df_list chứa kết quả dự đoán theo đúng thứ tự đầu vào
for i, pred_df in enumerate(pred_df_list):
    print(f"Dự báo cho chuỗi {i}:")
    print(pred_df.head())
```

**Các yêu cầu quan trọng đối với Batch Prediction:**
- Tất cả các chuỗi phải có cùng độ dài lịch sử (cửa sổ lookback).
- Tất cả các chuỗi phải có cùng độ dài dự đoán (`pred_len`).
- Mỗi DataFrame phải chứa đầy đủ các cột bắt buộc: `['open', 'high', 'low', 'close']`.
- Các cột `volume` và `amount` là tùy chọn; nếu thiếu sẽ tự động được điền bằng 0.

Phương thức `predict_batch` tận dụng khả năng xử lý song song trên GPU để tối ưu hóa hiệu năng, đồng thời tự động chuẩn hóa và giải chuẩn hóa độc lập cho từng chuỗi.

#### 5. Ví dụ và Trực quan hóa

Để chạy một script hoàn chỉnh bao gồm nạp dữ liệu, dự báo và vẽ biểu đồ, xem tại [`examples/prediction_example.py`](examples/prediction_example.py).

Khi chạy script này, một biểu đồ so sánh giữa dữ liệu thực tế (ground truth) và dự báo của mô hình sẽ được tạo ra, tương tự hình dưới đây:

<p align="center">
    <img src="figures/prediction_example.png" alt="Ví dụ Dự báo" align="center" width="600px" />
</p>

Ngoài ra, chúng tôi còn cung cấp script dự báo không cần dữ liệu Volume và Amount tại [`examples/prediction_wo_vol_example.py`](examples/prediction_wo_vol_example.py).


## 🔧 Tinh chỉnh (Finetuning) trên Dữ liệu Riêng của Bạn (Ví dụ Thị trường Cổ phiếu A-Share)

Chúng tôi cung cấp quy trình (pipeline) hoàn chỉnh để finetune Kronos trên các tập dữ liệu tùy chỉnh của bạn. Trong ví dụ này, chúng tôi minh họa cách sử dụng [Qlib](https://github.com/microsoft/qlib) để chuẩn bị dữ liệu từ thị trường cổ phiếu Trung Quốc (A-share) và chạy một thử nghiệm backtest đơn giản.

> **Tuyên bố miễn trừ trách nhiệm (Disclaimer):** Pipeline này mang tính minh họa quy trình fine-tuning. Đây là một ví dụ đơn giản hóa và **chưa phải là hệ thống giao dịch định lượng sẵn sàng cho môi trường thực tế (production)**. Một chiến lược định lượng mạnh mẽ đòi hỏi các kỹ thuật nâng cao hơn như tối ưu danh mục đầu tư (portfolio optimization) và trung hòa các yếu tố rủi ro (risk factor neutralization) để đạt được alpha ổn định.

Quá trình fine-tuning gồm 4 bước chính:

1.  **Cấu hình (Configuration)**: Thiết lập đường dẫn và siêu tham số (hyperparameters).
2.  **Chuẩn bị Dữ liệu (Data Preparation)**: Xử lý và phân chia dữ liệu bằng Qlib.
3.  **Finetune Mô hình (Model Finetuning)**: Huấn luyện tinh chỉnh Tokenizer và Predictor.
4.  **Kiểm thử Lịch sử (Backtesting)**: Đánh giá hiệu suất của mô hình sau khi finetune.

### Yêu cầu Tiên quyết

1. Đảm bảo đã cài đặt đầy đủ các thư viện trong `requirements.txt`.
2. Pipeline này phụ thuộc vào `qlib`. Vui lòng cài đặt:
    ```shell
    pip install pyqlib
    ```
3. Bạn cần chuẩn bị dữ liệu Qlib. Hãy làm theo [hướng dẫn chính thức của Qlib](https://github.com/microsoft/qlib) để tải và thiết lập dữ liệu cục bộ. Các script mẫu giả định bạn sử dụng dữ liệu tần suất theo ngày (daily).

### Bước 1: Cấu hình Thử nghiệm

Tất cả các cài đặt về dữ liệu, huấn luyện và đường dẫn mô hình được quản lý tập trung trong file `finetune/config.py`. Trước khi chạy bất kỳ script nào, vui lòng **chỉnh sửa các đường dẫn sau** phù hợp với môi trường của bạn:

*   `qlib_data_path`: Đường dẫn tới thư mục dữ liệu Qlib cục bộ.
*   `dataset_path`: Thư mục lưu các file pickle của tập train/validation/test sau khi xử lý.
*   `save_path`: Thư mục gốc để lưu các checkpoint mô hình.
*   `backtest_result_path`: Thư mục lưu kết quả backtest.
*   `pretrained_tokenizer_path` và `pretrained_predictor_path`: Đường dẫn tới các mô hình pre-trained ban đầu (có thể là đường dẫn máy tính hoặc tên mô hình trên Hugging Face).

Bạn cũng có thể điều chỉnh các tham số khác như `instrument`, `train_time_range`, `epochs`, và `batch_size` cho phù hợp với bài toán của bạn. Nếu không sử dụng [Comet.ml](https://www.comet.com/), hãy đặt `use_comet = False`.

### Bước 2: Chuẩn bị Tập Dữ liệu

Chạy script tiền xử lý dữ liệu. Script này sẽ đọc dữ liệu thị trường thô từ thư mục Qlib, xử lý, chia thành các tập train, validation, test và lưu thành file pickle.

```shell
python finetune/qlib_data_preprocess.py
```

Sau khi chạy xong, bạn sẽ thấy các file `train_data.pkl`, `val_data.pkl`, và `test_data.pkl` nằm trong thư mục được cấu hình tại `dataset_path`.

### Bước 3: Chạy Finetuning

Quá trình finetuning bao gồm 2 giai đoạn: finetune tokenizer trước, sau đó finetune predictor. Cả hai script huấn luyện đều hỗ trợ chạy đa GPU thông qua `torchrun`.

#### 3.1 Finetune Tokenizer

Bước này giúp điều chỉnh tokenizer cho thích ứng với phân phối dữ liệu thuộc lĩnh vực cụ thể của bạn.

```shell
# Thay NUM_GPUS bằng số lượng GPU muốn dùng (ví dụ: 2)
torchrun --standalone --nproc_per_node=NUM_GPUS finetune/train_tokenizer.py
```

Checkpoint tokenizer tốt nhất sẽ được lưu vào đường dẫn đã cấu hình trong `config.py` (tạo từ `save_path` và `tokenizer_save_folder_name`).

#### 3.2 Finetune Predictor

Bước này finetune mô hình Kronos chính cho tác vụ dự báo.

```shell
# Thay NUM_GPUS bằng số lượng GPU muốn dùng (ví dụ: 2)
torchrun --standalone --nproc_per_node=NUM_GPUS finetune/train_predictor.py
```

Checkpoint predictor tốt nhất sẽ được lưu vào đường dẫn tương ứng trong `config.py`.

### Bước 4: Đánh giá bằng Backtesting

Cuối cùng, chạy script backtesting để đánh giá mô hình sau khi tinh chỉnh. Script sẽ nạp mô hình, thực hiện suy luận trên tập test, tạo ra các tín hiệu dự báo (ví dụ: mức biến động giá dự kiến), và chạy kiểm thử một chiến lược top-K đơn giản.

```shell
# Chỉ định GPU cho quá trình suy luận
python finetune/qlib_test.py --device cuda:0
```

Script sẽ xuất ra phân tích hiệu năng chi tiết trên console và biểu đồ đường cong lợi nhuận lũy kế (cumulative return) so với chỉ số tham chiếu (benchmark), tương tự hình dưới:

<p align="center">
    <img src="figures/backtest_result_example.png" alt="Ví dụ Kết quả Backtest" align="center" width="700px" />
</p>

### 💡 Lưu ý Quan trọng: Từ Bản Demo đến Thực tế (Production)

*   **Tín hiệu Thô vs Alpha Thuần túy (Raw Signals vs. Pure Alpha)**: Các tín hiệu mô hình tạo ra trong demo này là những dự đoán thô. Trong quy trình giao dịch định lượng thực tế, các tín hiệu này thường cần được đưa vào mô hình tối ưu hóa danh mục đầu tư. Mô hình tối ưu sẽ áp dụng các ràng buộc để trung hòa mức độ phơi nhiễm đối với các yếu tố rủi ro chung (ví dụ: beta thị trường, các yếu tố phong cách như quy mô vốn hóa - size, giá trị - value), qua đó bóc tách được **"alpha thuần túy" (pure alpha)** và nâng cao tính bền vững cho chiến lược.
*   **Xử lý Dữ liệu**: Lớp `QlibDataset` được cung cấp dưới dạng ví dụ. Đối với các nguồn hoặc định dạng dữ liệu khác, bạn sẽ cần điều chỉnh lại logic nạp và tiền xử lý dữ liệu.
*   **Độ Phức tạp của Chiến lược và Backtesting**: Chiến lược top-K đơn giản ở đây chỉ là điểm khởi đầu cơ bản. Các chiến lược cấp độ production thường tích hợp logic phức tạp hơn về xây dựng danh mục, định cỡ vị thế linh hoạt (dynamic position sizing) và quản lý rủi ro (ví dụ: quy tắc cắt lỗ / chốt lời). Hơn nữa, một bài backtest chuẩn mực cần mô phỏng tỉ mỉ chi phí giao dịch, độ trượt giá (slippage) và tác động thị trường (market impact) để phản ánh chính xác hiệu suất thực tế.

> **📝 Chú thích do AI tạo**: Lưu ý rằng nhiều chú thích (comments) trong thư mục `finetune/` được sinh tự động bởi trợ lý AI (Gemini 2.5 Pro) cho mục đích giải thích. Mặc dù chúng nhằm mục đích hỗ trợ, nhưng vẫn có thể có chỗ chưa chính xác. Bạn nên dựa vào chính mã nguồn làm căn cứ logic chuẩn xác nhất.

## 📖 Trích dẫn (Citation)

Nếu bạn sử dụng Kronos trong các nghiên cứu khoa học của mình, vui lòng trích dẫn [bài báo](https://arxiv.org/abs/2508.02739):

```bibtex
@misc{shi2025kronos,
      title={Kronos: A Foundation Model for the Language of Financial Markets}, 
      author={Yu Shi and Zongliang Fu and Shuo Chen and Bohan Zhao and Wei Xu and Changshui Zhang and Jian Li},
      year={2025},
      eprint={2508.02739},
      archivePrefix={arXiv},
      primaryClass={q-fin.ST},
      url={https://arxiv.org/abs/2508.02739}, 
}
```

## 📜 Giấy phép (License)
Dự án này được phân phối dưới giấy phép [MIT License](./LICENSE).

