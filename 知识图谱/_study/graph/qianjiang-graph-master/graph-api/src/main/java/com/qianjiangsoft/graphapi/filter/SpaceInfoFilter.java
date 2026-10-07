package com.qianjiangsoft.graphapi.filter;

import cn.hutool.core.date.DateUnit;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSON;
import cn.hutool.json.JSONUtil;
import com.qianjiangsoft.graphcore.common.Result;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.engine.CardParamContextHolder;
import com.qianjiangsoft.graphcore.engine.GraphEngine;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import com.qianjiangsoft.graphcore.mapper.GraphSpaceMapper;
import com.qianjiangsoft.graphcore.service.SpacePersistenceService;
import com.qianjiangsoft.graphcore.service.SpaceSyncService;
import com.qianjiangsoft.graphcore.util.CommonServletUtil;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import javax.servlet.FilterChain;
import javax.servlet.ServletException;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.io.PrintWriter;

/**
 * space fileter
 * 
 * @author qianjiang
 */
@Component
@Slf4j
public class SpaceInfoFilter extends OncePerRequestFilter {

	@Autowired
	GraphEngine graphEngine;

	@Autowired
	GraphSpaceMapper graphSpaceMapper;

	@Autowired
	SpacePersistenceService spacePersistenceService;

	@Autowired
	SpaceSyncService spaceSyncService;

	long samplingVisitedTime = 1;

	@Override
	protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain) throws ServletException, IOException {

		//获取spaceId
		String spaceId = request.getParameter("spaceId");

		if(StrUtil.isEmpty(spaceId)) {
			spaceId = request.getHeader("spaceId");
		}

		if(StrUtil.isEmpty(spaceId)) {
			chain.doFilter(request, response);
			return;
		}

		log.info("访问接口:[{}],参数:spaceId:[{}]", request.getRequestURI(), spaceId);

		SpaceContextHolder.set(graphEngine.getSpaceMap().get(spaceId));

		GraphSpace graphSpace = graphSpaceMapper.selectById(spaceId);
		if(graphSpace != null) {

			//更新visited time
			if(graphSpace.getLastVisitedTime() == null){
				graphSpace.setLastVisitedTime(DateUtil.date());
				graphSpaceMapper.updateById(graphSpace);
			}else {
				long minutes = DateUtil.between(graphSpace.getLastVisitedTime(), DateUtil.date(), DateUnit.MINUTE);
				if(minutes > samplingVisitedTime) {
					graphSpace.setLastVisitedTime(DateUtil.date());
					graphSpaceMapper.updateById(graphSpace);
				}
			}
		}
		chain.doFilter(request, response);
		SpaceContextHolder.remove();
	}
}
