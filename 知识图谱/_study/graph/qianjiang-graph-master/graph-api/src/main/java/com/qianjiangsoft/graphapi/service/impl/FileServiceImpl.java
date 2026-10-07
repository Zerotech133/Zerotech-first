package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.date.DateUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.io.file.FileNameUtil;
import cn.hutool.core.util.ArrayUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphapi.service.FileService;
import com.qianjiangsoft.graphcore.config.ServerConfig;
import com.qianjiangsoft.graphcore.constants.Constants;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;

/**
 * 文件上传工具类
 *
 * @author qianjiang
 */
@Service
public class FileServiceImpl implements FileService {

	@Autowired
	ServerConfig serverConfig;



	/**
	 * 根据文件路径上传
	 *
	 * @param fileDir 相对应用的基目录
	 * @param file 上传的文件
	 * @return 文件名称
	 * @throws IOException
	 */
	@Override
	public String upload(String fileDir, MultipartFile file) throws IOException {
		return upload(fileDir, file, Constants.FileType.DEFAULT_ALLOWED_EXTENSION);
	}

	/**
	 * 文件上传
	 *
	 * @param fileDir 相对应用的基目录
	 * @param file 上传的文件
	 * @param allowedExtension 上传文件类型
	 * @return 返回上传成功的文件名
	 * @throws IOException 比如读写文件出错时
	 */
	@Override
	public String upload(String fileDir, MultipartFile file, String[] allowedExtension) throws IOException {

		int fileNameLength = file.getOriginalFilename().length();
		if (fileNameLength > FileServiceImpl.DEFAULT_FILE_NAME_LENGTH) {
			throw new BusinessException(ResultCode.FILE_ERROR, "文件名超出长度");
		}

		checkUploadFile(file);

		String extName = FileNameUtil.extName(file.getOriginalFilename());
		if("".equals(extName) && file.getContentType().substring(file.getContentType().lastIndexOf("/")+1)!=""){
			extName=file.getContentType().substring(file.getContentType().lastIndexOf("/")+1);
		}
		String fileName = IdUtil.simpleUUID() + "." + extName;

		//相对路径
		String filePath = fileDir + File.separatorChar +
				DateUtil.format(DateUtil.date(), "yyyy/MM/dd") + File.separatorChar + fileName;

		//绝对路径
		String fileRealPath = serverConfig.getBaseDir() + filePath;

		FileUtil.writeFromStream(file.getInputStream(), fileRealPath);
		return filePath;
	}


	/**
	 * 文件大小校验
	 *
	 * @param file 上传的文件
	 * @return
	 */
	@Override
	public void checkUploadFile(MultipartFile file) {
		long size = file.getSize();
		if (DEFAULT_MAX_SIZE != -1 && size > DEFAULT_MAX_SIZE) {
			throw new BusinessException(ResultCode.FILE_SIZE_ERROR, "文件大小超过限制");
		}

		String extension = FileNameUtil.extName(file.getOriginalFilename());

		if (!ArrayUtil.contains(Constants.FileType.DEFAULT_ALLOWED_EXTENSION, extension)) {
			if(!ArrayUtil.contains(Constants.FileType.DEFAULT_ALLOWED_EXTENSION,
					file.getContentType().substring(file.getContentType().lastIndexOf("/")+1))) {
				throw new BusinessException(ResultCode.FILE_TYPE_ERROR, "文件格式不允许");
			}
		}

	}


	/**
	 * 检查文件是否可下载
	 *
	 * @param filePath 需要下载的文件
	 * @return true 正常 false 非法
	 */
	@Override
	public void checkDownloadFile(String filePath) {
		// 禁止目录上跳级别
		if (StrUtil.contains(filePath, "..")) {
			throw new BusinessException(ResultCode.FILE_ERROR, "文件路径异常");
		}

		// 检查允许下载的文件规则
		if (ArrayUtil.contains(Constants.FileType.DEFAULT_ALLOWED_EXTENSION, FileNameUtil.getSuffix(filePath))) {
			throw new BusinessException(ResultCode.FILE_TYPE_ERROR, "文件类型不容许");
		}
	}

	@Override
	public String getDownloadFilePath(String filePath) {

		checkDownloadFile(filePath);

		String absoluteFilePath = serverConfig.getBaseDir() + filePath;
		return absoluteFilePath;
	}
}
