package com.qianjiangsoft.graphcore.page;

import cn.hutool.core.convert.Convert;
import cn.hutool.core.util.StrUtil;
import com.github.pagehelper.PageHelper;
import com.github.pagehelper.PageInfo;
import com.qianjiangsoft.graphcore.util.CommonServletUtil;

import java.util.List;

/**
 * 表格数据处理
 * 
 * @author qianjiang
 */
public class PageUtil {
	/**
	 * 当前记录起始索引
	 */
	public static final String PAGE_NUM = "pageNum";

	/**
	 * 每页显示记录数
	 */
	public static final String PAGE_SIZE = "pageSize";

	/**
	 * 排序列
	 */
	public static final String ORDER_BY_COLUMN = "orderBy";

	public static final int defaultPageNum = 1;
	public static final int defaultPageSize = 20;

	public static PageParam buildPageParam() {
		PageParam pageParam = new PageParam();

		// 获取参数
		Integer pageNum = Convert.toInt(CommonServletUtil.getParameter(PAGE_NUM));
		Integer pageSize = Convert.toInt(CommonServletUtil.getParameter(PAGE_SIZE));

		if (pageNum == null || pageNum < 0) {
			pageNum = defaultPageNum;
		}
		if (pageSize == null || pageSize < 0) {
			pageSize = defaultPageSize;
		}

		pageParam.setPageNum(pageNum);
		pageParam.setPageSize(pageSize);
		pageParam.setOrderBy(CommonServletUtil.getParameter(ORDER_BY_COLUMN));
		return pageParam;
	}

	/**
	 * 设置请求分页数据
	 */
	public static void startPage(Integer pageNum, Integer pageSize, String orderBy) {
		PageParam pageParam = PageUtil.buildPageParam();
		if (pageNum == null) {
			pageNum = pageParam.getPageNum();
		}
		if (pageSize == null) {
			pageSize = pageParam.getPageSize();
		}
		if (StrUtil.isEmpty(orderBy)) {
			orderBy = pageParam.getOrderBy();
		}
		PageHelper.startPage(pageNum, pageSize, orderBy);
	}

	/**
	 * 设置请求分页数据
	 */
	public static void startPage(Integer pageNum, Integer pageSize) {
		PageParam pageParam = PageUtil.buildPageParam();
		if (pageNum == null) {
			pageNum = pageParam.getPageNum();
		}
		if (pageSize == null) {
			pageSize = pageParam.getPageSize();
		}
		PageHelper.startPage(pageNum, pageSize);
	}

	public static void startPage(String orderBy) {
		startPage(null, null, orderBy);
	}

	public static void startPage() {
		startPage(null, null, null);
	}

	public static <T> PageDataVO<T> getPageDataVO(List<T> list) {
		return PageDataVO.<T> builder().list(list).total(PageInfo.of(list).getTotal()).build();
	}

	public static <T> PageDataVO<T> getPageDataVO(List<T> list, long total) {
		return PageDataVO.<T> builder().list(list).total(total).build();
	}
}
