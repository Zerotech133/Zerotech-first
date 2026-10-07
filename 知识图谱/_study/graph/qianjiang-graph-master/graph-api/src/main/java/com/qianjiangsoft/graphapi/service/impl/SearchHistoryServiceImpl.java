package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.date.DatePattern;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.text.StrPool;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.github.pagehelper.PageInfo;
import com.qianjiangsoft.graphapi.dto.SearchHistoryDTO;
import com.qianjiangsoft.graphapi.param.history.SearchHistoryParam;
import com.qianjiangsoft.graphapi.service.SearchHistoryService;
import com.qianjiangsoft.graphcore.domain.entity.SearchHistory;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.mapper.SearchHistoryMapper;
import com.qianjiangsoft.graphcore.model.Space;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import com.qianjiangsoft.graphcore.page.PageUtil;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class SearchHistoryServiceImpl implements SearchHistoryService {

    @Autowired
    SearchHistoryMapper searchHistoryMapper;


    @Override
    public PageDataVO<SearchHistoryDTO> searchHistory(SearchHistoryParam param) {

        //分页
        PageUtil.startPage();

        LambdaQueryWrapper<SearchHistory> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(SearchHistory::getSpaceId, SpaceContextHolder.getSpaceId())
                .eq(StrUtil.isNotEmpty(param.getType()), SearchHistory::getType, param.getType())
                .ge(param.getBeginTime() != null, SearchHistory::getCreateTime, param.getBeginTime())
                .le(param.getEndTime() != null, SearchHistory::getCreateTime, param.getEndTime())
                .orderByDesc(SearchHistory::getCreateTime);

        //转换对象
        List<SearchHistory> searchHistoryList = searchHistoryMapper.selectList(queryWrapper);

        return PageUtil.getPageDataVO(BeanUtil.copyToList(searchHistoryList, SearchHistoryDTO.class),
                PageInfo.of(searchHistoryList).getTotal());
    }

    /**
     * 搜索历史
     */
    @Override
    @Async
    public void addSearchHistory(String param,String type, Space space) {

        SearchHistory searchHistory = new SearchHistory();
        searchHistory.setId(IdUtil.simpleUUID());
        searchHistory.setType(type);

        String name = StrUtil.concat(true, type, StrPool.DASHED,
                DateUtil.format(DateUtil.date(), DatePattern.PURE_DATETIME_PATTERN));
        searchHistory.setName(name);
        searchHistory.setParam(param);
        searchHistory.setSpaceId(space.getId());
        searchHistory.setCreateTime(DateUtil.date());

        searchHistoryMapper.insert(searchHistory);
    }

    @Override
    public void deleteHistory(String id) {
        searchHistoryMapper.deleteById(id);
    }

}
