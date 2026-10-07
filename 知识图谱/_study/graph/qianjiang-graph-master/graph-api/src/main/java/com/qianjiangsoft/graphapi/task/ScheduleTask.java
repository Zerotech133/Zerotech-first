package com.qianjiangsoft.graphapi.task;

import cn.hutool.core.date.DateUnit;
import cn.hutool.core.date.DateUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.qianjiangsoft.graphcore.domain.entity.GraphSpace;
import com.qianjiangsoft.graphcore.mapper.GraphSpaceMapper;
import com.qianjiangsoft.graphcore.service.SpacePersistenceService;
import com.qianjiangsoft.graphcore.service.SpaceService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;


@Component
@Slf4j
public class ScheduleTask {

    @Autowired
    GraphSpaceMapper graphSpaceMapper;

    @Autowired
    SpaceService spaceService;

    @Autowired
    SpacePersistenceService spacePersistenceService;

    public static final int MAX_IDLE_HOURS = 24 * 30;
    /** 每1小时更新一下激活状态 */
    public static final String global_graph_id= "GRAPH_OVERALL_SITUATION_ID";

    /** 每1小时更新一下激活状态 */
    @Scheduled(fixedRate = 60 * 60 * 1000, initialDelay = 60 * 60 * 1000)
    private void monitorSpaceActiveStatus() {
        log.info("开始更新空间激活状态任务");

        //查询已经激活的空间列表
        LambdaQueryWrapper<GraphSpace> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(GraphSpace::getActiveFlag, 1);

        List<GraphSpace> graphSpaceList = graphSpaceMapper.selectList(queryWrapper);

        //卸载符合条件的空间
        for(GraphSpace graphSpace : graphSpaceList) {

            if(graphSpace.getId().equals(global_graph_id)) {
                continue;
            }

            if(graphSpace.getLastVisitedTime() != null) {
                long days = DateUtil.between(DateUtil.date(), graphSpace.getLastVisitedTime(), DateUnit.HOUR);
                if(days > MAX_IDLE_HOURS) {
                    log.info("开始卸载space[{}]", graphSpace.getId());

                    //移除space
                    spaceService.unloadSpace(graphSpace.getId());
                }
            }
        }

        log.info("结束更新空间激活状态任务");
    }
}