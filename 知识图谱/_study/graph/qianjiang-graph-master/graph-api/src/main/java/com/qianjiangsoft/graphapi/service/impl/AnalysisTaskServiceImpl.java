package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.github.pagehelper.PageInfo;
import com.qianjiangsoft.graphapi.constants.AnalyseTaskStatus;
import com.qianjiangsoft.graphapi.dto.AddGraphAnalyseTaskParam;
import com.qianjiangsoft.graphapi.dto.QueryGraphAnalyseTaskListDTO;
import com.qianjiangsoft.graphapi.param.analysisTask.SearchTaskParam;
import com.qianjiangsoft.graphapi.service.AnalysisTaskService;
import com.qianjiangsoft.graphcore.common.redis.RedisService;
import com.qianjiangsoft.graphcore.domain.entity.GraphAnalyseTask;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.mapper.GraphAnalyseTaskMapper;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import com.qianjiangsoft.graphcore.page.PageUtil;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

import static com.qianjiangsoft.graphapi.constants.RedisConstants.analyseTaskResultKeyPrefix;

@Service
public class AnalysisTaskServiceImpl implements AnalysisTaskService {

    @Autowired
    GraphAnalyseTaskMapper analyseTaskMapper;

    @Autowired
    RedisService redisService;

    @Override
    public PageDataVO<QueryGraphAnalyseTaskListDTO> queryListByParam(SearchTaskParam param) {

        //分页
        PageUtil.startPage();

        LambdaQueryWrapper<GraphAnalyseTask> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphAnalyseTask::getSpaceId, SpaceContextHolder.getSpaceId())
                .eq(StrUtil.isNotEmpty(param.getType()), GraphAnalyseTask::getType, param.getType())
                .ge(param.getBeginTime() != null, GraphAnalyseTask::getCreateTime, param.getBeginTime())
                .le(param.getEndTime() != null, GraphAnalyseTask::getCreateTime, param.getEndTime())
                .orderByDesc(GraphAnalyseTask::getCreateTime);

        //转换对象
        List<GraphAnalyseTask> analyseTaskList = analyseTaskMapper.selectList(queryWrapper);

        return PageUtil.getPageDataVO(BeanUtil.copyToList(analyseTaskList, QueryGraphAnalyseTaskListDTO.class),
                PageInfo.of(analyseTaskList).getTotal());
    }

    @Override
    public Integer addAnalysisTask(AddGraphAnalyseTaskParam param) {

        GraphAnalyseTask analyseTask = BeanUtil.copyProperties(param, GraphAnalyseTask.class);
        analyseTask.setCreateTime(DateUtil.date());
        analyseTask.setDelFlag(false);
        analyseTask.setStatus(AnalyseTaskStatus.running);
        analyseTaskMapper.insert(analyseTask);

        return analyseTask.getId();
    }

    @Override
    public void deleteTask(Integer id) {
        analyseTaskMapper.deleteById(id);
    }

    @Override
    public Object queryTaskResult(Integer id) {
        String key = analyseTaskResultKeyPrefix + id;
        return redisService.get(key);
    }

    @Override
    public void updateAnalysisTaskStatus(Integer id, Integer status) {
        GraphAnalyseTask analyseTask = new GraphAnalyseTask();
        analyseTask.setStatus(status);
        analyseTask.setId(id);
        analyseTask.setFinishTime(DateUtil.date());
        analyseTaskMapper.updateById(analyseTask);
    }
}
