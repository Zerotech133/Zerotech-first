package com.qianjiangsoft.graphapi.controller;

import cn.hutool.core.io.FileUtil;
import com.qianjiangsoft.graphapi.service.FileService;
import com.qianjiangsoft.graphcore.util.CommonServletUtil;
import io.swagger.annotations.Api;
import org.assertj.core.util.Lists;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import javax.servlet.http.HttpServletResponse;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 通用请求处理
 * 
 * @author qianjiang
 */
@Api("通用请求处理")
@RequestMapping("/file")
@RestController
public class FileController {

	private static final Logger log = LoggerFactory.getLogger(FileController.class);

	@Autowired
	FileService fileService;

	private static final String uploadDir = "/upload";

	/**
	 * 通用下载请求
	 * 
	 * @param filePath 文件名称
	 */
	@GetMapping("/download")
	public void fileDownload(String filePath, HttpServletResponse response) throws Exception {
		String absoluteFilePath = fileService.getDownloadFilePath(filePath);
		response.setContentType(MediaType.APPLICATION_OCTET_STREAM_VALUE);
		CommonServletUtil.setAttachmentResponseHeader(response, FileUtil.getName(filePath),false);
		FileUtil.writeToStream(absoluteFilePath, response.getOutputStream());
	}

	/**
	 * 通用上传请求
	 */
	@PostMapping("/upload")
	public Map uploadFile(MultipartFile file) throws Exception {
		// 上传并返回新文件名称
		String filePath = fileService.upload(uploadDir, file);
		Map result = new HashMap();
		result.put("filePath", filePath);
		result.put("fileName", file.getOriginalFilename());
		return result;
	}

	/**
	 * 批量文件上传
	 */
	@PostMapping("/upload/batch")
	public List<Map<String, Object>> upload(@RequestParam("file") MultipartFile[] files) throws Exception{
		// 数据组装对象
		Map<String, Object> result = new HashMap<>();
		List<Map<String, Object>> data = Lists.newArrayList();
		// 遍历文件，执行上传操作
		for (MultipartFile file : files) {
			// 上传并返回新文件名称
			String filePath = fileService.upload(uploadDir, file);
			result.put("fileName", file.getOriginalFilename());
			result.put("filePath", filePath);
			data.add(result);
		}
		return data;
	}

}
