package com.qianjiangsoft.graphcore.util;

import cn.hutool.core.util.CharsetUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.core.util.URLUtil;
import org.apache.poi.ss.usermodel.Workbook;
import org.springframework.web.context.request.RequestAttributes;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.UnsupportedEncodingException;
import java.net.URLEncoder;

/**
 * 客户端工具类
 * 
 * @author qianjiang
 */
public class CommonServletUtil {
	/**
	 * 获取String参数
	 */
	public static String getParameter(String name) {
		return getRequest().getParameter(name);
	}

	public static String getParameter(String name, String defaultValue) {
		String param = getRequest().getParameter(name);
		if(StrUtil.isEmpty(param)) {
			return defaultValue;
		}
		return param;
	}

	/**
	 * 获取request
	 */
	public static HttpServletRequest getRequest() {
		return getRequestAttributes().getRequest();
	}

	/**
	 * 获取response
	 */
	public static HttpServletResponse getResponse() {
		return getRequestAttributes().getResponse();
	}

	public static ServletRequestAttributes getRequestAttributes() {
		RequestAttributes attributes = RequestContextHolder.getRequestAttributes();
		return (ServletRequestAttributes) attributes;
	}

	/**
	 * 将字符串渲染到客户端
	 * 
	 * @param response 渲染对象
	 * @param string 待渲染的字符串
	 * @return null
	 */
	public static String writerContent(HttpServletResponse response, String string) {
		try {
			response.setStatus(200);
			response.setContentType("application/json");
			response.setCharacterEncoding("utf-8");
			response.getWriter().print(string);
		} catch (IOException e) {
			e.printStackTrace();
		}
		return null;
	}


	public static String getBaseUrl(HttpServletRequest request) {
		return request.getScheme() +"://" + request.getServerName() + ":" +
				request.getServerPort() + request.getServletContext().getContextPath();
	}

	/**
	 * 下载文件名重新编码
	 *
	 * @param response     响应对象
	 * @param realFileName 真实文件名
	 * @return
	 */
	/**
	 * 下载文件名重新编码
	 * @param response 响应对象
	 * @param realFileName 真实文件名
	 * @param flag 是否是excel文件
	 */
	public static void setAttachmentResponseHeader(HttpServletResponse response, String realFileName, boolean flag) throws UnsupportedEncodingException {

		if (flag) {
			response.setCharacterEncoding("UTF-8");
			response.setHeader("content-Type", "application/ms-excel");
			response.setHeader("Content-Disposition", "attachment;filename=" + URLEncoder.encode(realFileName, "UTF-8"));
			response.setHeader("Access-Control-Expose-Headers", "Content-Disposition");
		} else {
			String percentEncodedFileName = URLUtil.encode(realFileName, CharsetUtil.CHARSET_UTF_8);

			StringBuilder contentDispositionValue = new StringBuilder();
			contentDispositionValue.append("attachment; filename=")
					.append(percentEncodedFileName)
					.append(";")
					.append("filename*=")
					.append("utf-8''")
					.append(percentEncodedFileName);

			response.setHeader("Content-disposition", contentDispositionValue.toString());
		}
	}


}
