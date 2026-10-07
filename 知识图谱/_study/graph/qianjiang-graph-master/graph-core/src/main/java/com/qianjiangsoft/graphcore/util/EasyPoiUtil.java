package com.qianjiangsoft.graphcore.util;

import cn.afterturn.easypoi.excel.ExcelExportUtil;
import cn.afterturn.easypoi.excel.ExcelImportUtil;
import cn.afterturn.easypoi.excel.entity.ExportParams;
import cn.afterturn.easypoi.excel.entity.ImportParams;
import cn.afterturn.easypoi.excel.entity.enmus.ExcelType;
import cn.afterturn.easypoi.excel.entity.params.ExcelExportEntity;
import org.apache.commons.lang3.StringUtils;
import org.apache.poi.ss.usermodel.Workbook;
import org.springframework.web.multipart.MultipartFile;

import javax.servlet.http.HttpServletResponse;
import java.io.File;
import java.io.IOException;
import java.util.List;
import java.util.Map;

/**
 * easyPoi 工具类
 * 
 * @author YoCiyy
 * @date 2022-06-08
 */
public final class EasyPoiUtil {

	private static void downLoadExcel(String fileName, HttpServletResponse response, Workbook workbook) {
		try {
			CommonServletUtil.setAttachmentResponseHeader(response, fileName, true);
			workbook.write(response.getOutputStream());
		} catch (IOException e) {
			throw new RuntimeException(e);
		}
	}

	public static <T> void exportAndDownload(List<T> dataList, Class<?> clz, String fileName, HttpServletResponse response, ExportParams exportParams) {
		exportParams.setType(ExcelType.HSSF);
		Workbook workbook = ExcelExportUtil.exportExcel(exportParams, clz, dataList);
		if (workbook != null) {
			downLoadExcel(fileName, response, workbook);
		}
	}

	/**
	 *
	 * @param dataList  数据
	 * @param collumList 表头
	 * @param fileName 文件名
	 * @param response response
	 * @param exportParams 导出参数（标题、sheet名称、是否创建表头，表格类型）
	 */
	public static <T> void exportAndDownload(List<T> dataList, List<ExcelExportEntity> collumList,
										 String fileName, HttpServletResponse response, ExportParams exportParams) {
		exportParams.setType(ExcelType.HSSF);
		Workbook workbook = ExcelExportUtil.exportExcel(exportParams, collumList, dataList);
		downLoadExcel(fileName, response, workbook);
	}

	public static void exportAndDownload(List<Map<String, Object>> dataList, String fileName, HttpServletResponse response) {
		Workbook workbook = ExcelExportUtil.exportExcel(dataList, ExcelType.HSSF);
		if (workbook != null) {
			downLoadExcel(fileName, response, workbook);
		}
	}

	public static <T> List<T> importExcel(String filePath, Integer titleRows, Integer headerRows, Class<T> clz) {
		if (StringUtils.isBlank(filePath)) {
			return null;
		}

		ImportParams params = new ImportParams();
		params.setTitleRows(titleRows);
		params.setHeadRows(headerRows);

		try {
			return ExcelImportUtil.importExcel(new File(filePath), clz, params);
		} catch (Exception e) {
			throw new RuntimeException(e.getMessage());
		}
	}

	public static <T> List<T> importExcel(MultipartFile file, Integer titleRows, Integer headerRows, Class<T> clz) {
		if (file == null) {
			return null;
		}

		ImportParams params = new ImportParams();
		params.setTitleRows(titleRows);
		params.setHeadRows(headerRows);

		try {
			return ExcelImportUtil.importExcel(file.getInputStream(), clz, params);
		} catch (Exception e) {
			throw new RuntimeException(e);
		}
	}

	public static <T> List<T> importExcel(MultipartFile file, Class<T> clz) {
		if (file == null) {
			return null;
		}

		ImportParams params = new ImportParams();
		params.setTitleRows(1);
		params.setHeadRows(1);
		try {
			return ExcelImportUtil.importExcel(file.getInputStream(), clz, params);
		} catch (Exception e) {
			throw new RuntimeException(e);
		}
	}
}
