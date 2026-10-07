package com.qianjiangsoft.graphapi.filter;

import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.text.StrPool;
import cn.hutool.core.thread.ThreadUtil;
import cn.hutool.core.util.StrUtil;
import com.qianjiangsoft.graphcore.engine.CardParamContextHolder;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;

/**
 * space fileter
 * 
 * @author qianjiang
 */
@Component
@Slf4j
public class CardQueryParamFilter extends OncePerRequestFilter {

	@Override
	protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain) throws ServletException, IOException {

		//获取card
		String cardKey = request.getHeader("cardKey");

		List<String> cardValueList = StrUtil.split(request.getHeader("cardValueList"), StrUtil.COMMA);

		if(StrUtil.isNotEmpty(cardKey) && CollUtil.isNotEmpty(cardValueList)) {
			log.info("过滤器加入卡片信息,cardKey:{}, cardList: {}", cardKey, StrUtil.join(StrPool.COMMA, cardValueList));
			CardParamContextHolder.set(cardKey, cardValueList);
		}
		chain.doFilter(request, response);
		CardParamContextHolder.remove();
	}
}
