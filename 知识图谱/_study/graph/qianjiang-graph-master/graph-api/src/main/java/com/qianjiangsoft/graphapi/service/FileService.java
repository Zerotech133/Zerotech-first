package com.qianjiangsoft.graphapi.service;

import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;

public interface FileService {
    /**
     * 默认大小 50M
     */
    long DEFAULT_MAX_SIZE = 50 * 1024 * 1024;
    /**
     * 默认的文件名最大长度 100
     */
    int DEFAULT_FILE_NAME_LENGTH = 100;

    String upload(String baseDir, MultipartFile file) throws IOException;

    String upload(String baseDir, MultipartFile file, String[] allowedExtension) throws IOException;

    void checkUploadFile(MultipartFile file);

    void checkDownloadFile(String filePath);

    String getDownloadFilePath(String filePath);
}
